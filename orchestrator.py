"""
orchestrator.py - Main Orchestrator Agent with Persistent Conversation Memory.

Architecture
------------
Two separate persistence layers:

1. **Application chat history** (chat_store.py):
   Raw messages and summaries stored in invoices.db. Used to:
   - Display conversation history to the user.
   - Build summarised context for the LLM on each request.

2. **LangGraph graph state** (sqlite_checkpointer.py):
   Internal agent state (tool calls, intermediate steps) stored in invoices.db
   under a stable thread_id derived from the conversation_id. Used by the
   ReAct agent to restore mid-execution state across restarts.

Context injection strategy:
   On each request, the orchestrator:
   a) Runs summarisation if the threshold is reached.
   b) Builds a context block: [summary (as system message)] + [recent verbatim messages].
   c) Appends the new user message.
   d) Invokes the LangGraph agent with this message list + the stable thread config.

   The LangGraph checkpointer handles short-term within-graph state; the
   chat_store + conversation_memory layer handles long-term cross-restart context.
"""

import logging
import os
import uuid
import datetime
from typing import Optional

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent

from config import (
    GROQ_API_KEY,
    GROQ_MODEL_NAME,
    CHAT_DB_PATH,
    SUMMARISE_THRESHOLD,
    RECENT_WINDOW,
)
from chat_store import (
    init_db,
    get_or_create_conversation,
    save_message,
    get_messages,
    list_conversations,
)
from conversation_memory import maybe_summarise, build_context_block
from sqlite_checkpointer import SqliteCheckpointer

from qa_agent import QAAgent as _QAAgent
from ocr_engine import OCRAgent as _OCRAgent
from graph_agent import GraphAgent as _GraphAgent
from report_generator import ReportAgent as _ReportAgent

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton checkpointer (one per process, thread-safe)
# ─────────────────────────────────────────────────────────────────────────────
_checkpointer: Optional[SqliteCheckpointer] = None


def _get_checkpointer() -> SqliteCheckpointer:
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = SqliteCheckpointer(db_path=CHAT_DB_PATH)
    return _checkpointer


# ─────────────────────────────────────────────────────────────────────────────
# Main Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """
# Role: Main Orchestrator Agent

You are the Main Orchestrator Agent for an AI-powered Invoice Automation System. Your responsibility is to understand user requests, select and coordinate specialist tools, manage relevant conversation context, and provide accurate, consistent results.

## 1. Available Tools

You have access to the following specialist tools:

1. **answer_invoice_question**: Answers questions about previously processed invoices using stored invoice data and the vector database.
2. **extract_data_ocr**: Processes invoice files, extracts invoice information using OCR, and stores the extracted data through the existing invoice-processing pipeline.
3. **generate_excel**: Generates Excel reports based on user requirements, including date ranges, seller names, invoice totals, and other relevant filters.
4. **generate_graph**: Generates charts and visualizations based on invoice data and the user's requested analysis.
5. **invoice_aggregate**: Computes EXACT counts, sums, averages, breakdowns, and per-vendor line-item unit prices in Python over stored invoices. Mandatory for every totals/counts/averages/exclusions/recency/line-item-rate question (see section 6). Never do arithmetic yourself.

Use each tool according to its purpose. Do not call every tool for every request.

## 2. Understand and Route User Requests

- Analyze the user's intent before selecting tools.
- Use `extract_data_ocr` to process new invoice files.
- Use `answer_invoice_question` to answer questions about existing invoice records and financial information.
- Use `generate_excel` to generate Excel reports.
- Use `generate_graph` to create charts, graphs, and visual analyses.
- Preserve the user's requested metrics, filters, date ranges, and output formats.
- If essential information is missing, ask a concise clarifying question rather than making assumptions.
- Supply each tool with the correct arguments required by its interface.

## 6. Exact Numbers: Always Use `invoice_aggregate`

`invoice_aggregate` computes exact counts, sums, averages, and breakdowns in
Python over the user's stored invoices. Use it for ANY question involving:
totals ("total spend", "how much in total"), counts ("how many invoices",
"how many unpaid"), averages, per-vendor or per-status sums, exclusions
("excluding Sharma", "other than NexGen"), recency ("last uploaded",
"most recent", "last 3 invoices"), and line-item rates ("at what rate is
the Wireless Mouse sold by each vendor", "price per vendor for item X").

- NEVER add up amounts yourself from the summary matrix or from retrieved
  chunks, and NEVER estimate a total from a few retrieved invoices. Call
  `invoice_aggregate` and quote its numbers verbatim.
- For recency questions ("last uploaded", "most recent N"), pass
  {"sort": "uploaded_at_desc", "limit": N}. `uploaded_at` is the ingestion
  timestamp and is the ONLY correct ordering for "last uploaded". Do NOT
  sort by invoice_date for recency questions.
- For "last but one" / "second most recent", request limit 2 and take the
  second entry.
- For before-tax questions, use the tool's "subtotal" field. If subtotal is
  0 or missing for an invoice, say "not extracted" - NEVER report
  total_amount as the subtotal.
- If the tool's "disambiguation" field says a vendor filter matched multiple
  vendors, present each vendor's total separately and ask the user which one
  they meant. NEVER merge them into one number silently.
- For line-item rate/price questions, pass {"line_item": "<product name>"}
  (optionally combined with "vendor" to narrow to one seller). The tool
  returns per-vendor unit_prices computed in Python: quote each vendor's
  prices verbatim, one vendor at a time, never merged. If the tool returns
  no matching vendors, say the item was not found - never invent a rate,
  and never ask the user to narrow down a question the tool already answered.
- After getting the tool result, you may call `answer_invoice_question` for
  surrounding context, but the numbers must come from `invoice_aggregate`.

## 3. Parallel Tool Execution

When a user request contains multiple independent tasks, request the relevant tool calls in the same agent step so they can execute concurrently whenever the model and execution framework support parallel tool calls.

Examples:

**Example A: Seller analysis and graph**

User: "Find the top five sellers by total invoice amount and generate a graph showing them."

- Use `answer_invoice_question` to retrieve the top five sellers and their total invoice amounts.
- Use `generate_graph` to generate the requested visualization.
- These tasks may execute concurrently because both agents can independently access the same database snapshot.

**Example B: Data analysis and Excel report**

User: "Tell me the total invoice amount for September and create an Excel report."

- Use the relevant tools concurrently when both tasks have sufficient information to proceed independently.

**Example C: Sequential processing**

User: "Process this invoice and then tell me its total amount."

- First use `extract_data_ocr` to process the invoice.
- Then use `answer_invoice_question` to retrieve its stored details after the data becomes available.

Rules for execution:

- Execute independent tasks concurrently whenever possible.
- Execute dependent tasks in the correct order.
- Do not unnecessarily serialize independent operations.
- Avoid redundant database queries when results can be reused.
- Never claim that tasks executed concurrently unless the execution mechanism actually ran them concurrently.

## 4. Shared Database and Data Consistency

- Specialist agents can independently access the shared database and database snapshot.
- Use consistent definitions, filters, date ranges, and aggregation rules across all tools.
- For seller rankings, distinguish between total invoice amount, invoice count, average invoice value, and other possible metrics.
- When a graph or Excel report represents previously requested results, preserve the same selection criteria and filters.
- Reuse previous results when appropriate and available, rather than repeating unnecessary work.
- If agents independently retrieve the same information, ensure their outputs remain consistent.
- Never fabricate, alter, or assume invoice records, seller names, dates, amounts, or numerical results.

## 5. Conversation History and Context Management

The orchestrator may receive recent conversation history, a conversation summary, and structured context from previous tasks.

Use this information to maintain continuity across user requests.

### Interpreting follow-up requests

Resolve references such as:

- "Show me the top five sellers."
- "Now generate a graph for them."
- "Export those results to Excel."
- "Use the same date range."
- "Filter it to just one seller."

Interpret follow-up requests using relevant earlier messages, decisions, and tool results.

### Passing context to specialist agents

1. Pass each specialist the relevant context required to complete its task accurately.
2. Include previous questions, seller names, invoice references, date ranges, metrics, filters, chart preferences, and report requirements when applicable.
3. When a task depends on an earlier result, preserve its meaning and constraints when invoking the next tool.
4. Do not assume specialist agents automatically have access to the orchestrator's conversation history. Provide the necessary context through their tool arguments or another supported state mechanism.
5. Keep the context concise and relevant. Do not forward unrelated historical messages unnecessarily.
6. If an ambiguous reference cannot be resolved reliably, ask the user for clarification.

### Context reliability

- Treat conversation history and summaries as context, not as authoritative invoice records.
- Use the appropriate tool to retrieve or verify invoice information when necessary.
- Do not invent missing information based on a summary.
- Preserve relevant user requirements across follow-up requests until the user changes them or the task is completed.

## 6. Task Dependencies and Result Coordination

- Identify which tasks can run independently and which depend on earlier results.
- Pass the necessary input and context to dependent tasks only after their prerequisites are complete.
- When multiple tools return results, combine them into a coherent response.
- When the user requests both numerical results and a graph, ensure the visualization represents the requested metric and filters.
- When the user requests an Excel report based on earlier analysis, preserve the applicable criteria and output requirements.
- If a tool fails, explain the failure accurately and provide any successfully completed results.
- Do not claim that a file was generated, data was stored, or a task was completed unless the tool result supports that claim.

## 7. Final Response

- Present results in clear, concise, user-friendly language.
- Include relevant numerical values, currency, units, and filters when available.
- When appropriate, summarize the analysis before presenting the visualization or report.
- Provide file references or paths returned by tools when available.
- Clearly communicate partial results, missing information, and errors.
- Do not expose internal reasoning or unnecessary implementation details.

## 8. General Behavior

- Be accurate, professional, and concise.
- Prioritize reliable results and efficient execution.
- Avoid unnecessary tool calls.
- Do not substitute assumptions or general knowledge for actual invoice data.
- Do not claim successful execution without supporting tool results.
## 9. Currency and Data Presentation Rules

- These are Indian invoices. Always use the Indian Rupee symbol (₹) for monetary amounts, formatted with comma grouping (e.g., ₹1,250.00, ₹50,000.00). Never use dollar ($) signs.
- When listing vendors or sellers, always output clean, human-readable vendor names. Never display raw Python dictionaries or JSON strings like `{'name': '...'}` or `[object Object]`.

## Primary Objective

Act as an intelligent coordinator between the user and the specialist agents. Understand the current request in the context of the conversation, route it to the correct tools, execute independent tasks concurrently whenever possible, respect task dependencies, and deliver accurate, consistent results.
"""


def _line_item_rates(entries, item_q):
    """Aggregate matching line items by vendor, in Python.

    entries: list of (vendor_name, line_items) tuples, already filtered.
    item_q: substring matched case-insensitively against the item description.

    Returns a dict with per-vendor distinct unit prices plus match stats.
    Line-item dicts use varied key names across documents, so description is
    read from name/description/desc/item/item_name/product and the unit price
    from rate/unit_price/price/unit_rate.
    """
    q = str(item_q or "").strip().lower()
    vendors = {}
    total_lines = 0
    invoices_with_match = 0

    def _fnum(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    for vendor, items in entries:
        if not isinstance(items, list):
            continue
        matched_here = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            desc = str(
                it.get("name") or it.get("description") or it.get("desc")
                or it.get("item") or it.get("item_name") or it.get("product") or ""
            )
            if q not in desc.lower():
                continue
            price = it.get("rate")
            if price is None:
                price = it.get("unit_price")
            if price is None:
                price = it.get("price")
            if price is None:
                price = it.get("unit_rate")
            price = _fnum(price)
            if price is not None:
                price = round(price, 2)
            qty = _fnum(it.get("qty") if it.get("qty") is not None else it.get("quantity"))
            v = vendor or "Unknown"
            b = vendors.setdefault(v, {"unit_prices": [], "matching_lines": 0})
            if price is not None and price not in b["unit_prices"]:
                b["unit_prices"].append(price)
                b["unit_prices"].sort()
            b["matching_lines"] += 1
            matched_here += 1
            total_lines += 1
        if matched_here:
            invoices_with_match += 1
    return {
        "mode": "line_item",
        "line_item_query": str(item_q or "").strip(),
        "vendors": vendors,
        "total_matching_lines": total_lines,
        "invoices_with_match": invoices_with_match,
    }


class MainOrchestrator:
    """
    Main Orchestrator with persistent conversation memory and LangGraph checkpointing.

    Parameters
    ----------
    user_id : str
        Identifier for the user.  Used to enforce conversation isolation in chat_store.
    conversation_id : str, optional
        Stable conversation identifier.  A new UUID is generated if not provided.
    db_path : str, optional
        Path to the SQLite database.  Defaults to CHAT_DB_PATH from config.
    summarise_threshold : int, optional
        Number of messages before summarisation triggers.
    recent_window : int, optional
        Number of recent messages kept verbatim after summarisation.
    """

    def __init__(
        self,
        user_id: str = "default_user",
        conversation_id: Optional[str] = None,
        db_path: Optional[str] = None,
        summarise_threshold: int = SUMMARISE_THRESHOLD,
        recent_window: int = RECENT_WINDOW,
    ):
        self.user_id = user_id
        self.db_path = db_path or CHAT_DB_PATH
        self.summarise_threshold = summarise_threshold
        self.recent_window = recent_window

        # Initialise DB schema
        init_db(self.db_path)

        # Get or create conversation
        import uuid
        self.conversation_id = get_or_create_conversation(
            conversation_id=conversation_id or str(uuid.uuid4()),
            user_id=self.user_id,
            db_path=self.db_path,
        )

        # thread_id for LangGraph checkpointer = conversation_id (stable, unique per conversation)
        self.thread_id = self.conversation_id

        # Build the LLM with capped max_tokens to prevent 8,000 token TPM exhaustion
        self._llm = ChatGroq(
            model=GROQ_MODEL_NAME,
            api_key=GROQ_API_KEY,
            temperature=0,
            max_tokens=1000,
        )

        # Build specialist agents (scoped to this orchestrator's user)
        self._qa_agent = _QAAgent(user_id=self.user_id)
        self._ocr_agent = _OCRAgent()
        self._graph_agent = _GraphAgent(llm=self._llm)
        self._graph_agent.user_id = self.user_id
        self._report_agent = _ReportAgent(user_id=self.user_id)

        # Build tools
        self._tools = self._create_tools()

        # Build ReAct agent with SQLite checkpointer
        self._agent = create_react_agent(
            model=self._llm,
            tools=self._tools,
            prompt=_SYSTEM_PROMPT,
            checkpointer=_get_checkpointer(),
        )

    # ─────────────────────────── Tools ───────────────────────────────

    def _create_tools(self) -> list:
        qa = self._qa_agent
        ocr = self._ocr_agent
        graph = self._graph_agent
        report = self._report_agent

        @tool
        def answer_invoice_question(question: str) -> str:
            """Answer questions about stored invoices using the vector database and invoice records."""
            result = qa.answer(question)
            return str(result.get("answer", result))

        @tool
        def extract_data_ocr(file: str, store: bool = True) -> str:
            """
            Extract invoice data from a PDF or image file.
            Use store=True to extract AND index the invoice into the database (when user wants to add it).
            Use store=False to extract ONLY without storing (when checking for duplicates or answering a one-time question about the file).
            Always returns the full extracted invoice data so you can use it for further reasoning.
            """
            if store:
                result = ocr.process_and_index_invoice(file, user_id=self.user_id)
            else:
                result = ocr.extract_only(file)

            status = result.get("status", "failed")
            message = result.get("message", "")
            extracted = result.get("extracted_data", {})

            if status != "success" or not extracted:
                return f"[{status}] {message}"

            # Format extracted data as readable string for LLM context
            lines = [f"[{status}] {message}", "\nExtracted Invoice Data:"]
            for key, val in extracted.items():
                if val is not None and val != "" and val != [] and val != {}:
                    lines.append(f"  {key}: {val}")
            if not store:
                lines.append("\nNote: This invoice was NOT stored in the database.")
            return "\n".join(lines)

        @tool
        def generate_excel(query: str) -> str:
            """Generate an Excel report of invoices matching a description (date range, vendor, status, etc.). Saves the report and returns the download filename."""
            import datetime as _dt
            import uuid as _uuid
            _timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            _uid = str(_uuid.uuid4())[:8]
            _filename = f"invoice_report_{_timestamp}_{_uid}.xlsx"
            _reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
            os.makedirs(_reports_dir, exist_ok=True)
            _output_path = os.path.join(_reports_dir, _filename)
            result = report.generate_report(query, output_path=_output_path)
            _status = result.get("status", "failed")
            _msg = result.get("message", "")
            if _status == "success":
                return f"{_msg} Download filename: {_filename}"
            return f"[{_status}] {_msg}"

        @tool
        def generate_graph(query: str) -> str:
            """Generate a chart or visualization of invoice data based on a natural language prompt."""
            result = graph.run(query)
            status = result.get("status", "unknown")
            msg = result.get("message", "")
            chart_paths = result.get("chart_paths", [])
            chart_tags = ""
            if chart_paths:
                for cp in chart_paths:
                    chart_tags += f"\n[CHART: {cp}]"
            return f"[{status}] {msg}{chart_tags}"

        @tool
        def invoice_aggregate(filter_json: str) -> str:
            """Compute EXACT invoice statistics in Python over the user's stored invoices.

            Use this for ANY question about totals, counts, averages, sums,
            per-vendor or per-status breakdowns, exclusions, recency, or the
            rate/price of a specific line item (product) per vendor.
            NEVER add up amounts yourself; ALWAYS call this tool and quote
            its numbers verbatim.

            filter_json: JSON object, all keys optional:
              - "vendor": substring matched case-insensitively against vendor_name
              - "payment_status": e.g. "Paid", "Unpaid", "Pending", "Overdue"
              - "date_from", "date_to": "YYYY-MM-DD" bounds on invoice_date (inclusive)
              - "sort": "uploaded_at_desc" to list most recently uploaded first
              - "limit": max invoices to list (use with sort)
              - "line_item": substring matched case-insensitively against line-item
                descriptions, e.g. "Wireless Mouse". When set, the tool switches
                to line-item mode and returns per-vendor unit prices for the
                matching items instead of invoice-level totals. Combine with
                "vendor" to narrow to one seller.

            Returns JSON with count, total_amount, subtotal, tax, discount,
            average_total, by_vendor and by_status breakdowns, and (when a
            limit is set) the matching invoice list. In line-item mode returns
            mode="line_item" with per-vendor unit_prices and match stats.
            """
            import json as _json
            try:
                f = _json.loads(filter_json) if isinstance(filter_json, str) else dict(filter_json or {})
            except Exception:
                return _json.dumps({"error": "filter_json must be valid JSON"})
            if not isinstance(f, dict):
                return _json.dumps({"error": "filter_json must be a JSON object"})

            from vector_store import get_all_invoices, normalize_invoice_data

            def _num(x):
                try:
                    return float(x or 0.0)
                except (TypeError, ValueError):
                    return 0.0

            vendor_q = str(f.get("vendor") or "").strip().lower()
            status_q = str(f.get("payment_status") or "").strip().lower()
            date_from = str(f.get("date_from") or "").strip()
            date_to = str(f.get("date_to") or "").strip()
            sort = str(f.get("sort") or "").strip()
            item_q = str(f.get("line_item") or "").strip()
            try:
                limit = int(f.get("limit") or 0)
            except (TypeError, ValueError):
                limit = 0

            recs = []
            line_entries = []  # (vendor_name, line_items) for line-item mode
            for inv in get_all_invoices(user_id=self.user_id):
                norm = normalize_invoice_data(inv, file_name=inv.get("file_name", ""))
                vendor = str(norm.get("vendor_name") or "")
                status = str(norm.get("payment_status") or "")
                inv_date = str(norm.get("invoice_date") or "")
                if vendor_q and vendor_q not in vendor.lower():
                    continue
                if status_q and status_q != status.lower():
                    continue
                if date_from and inv_date < date_from:
                    continue
                if date_to and inv_date > date_to:
                    continue
                if item_q:
                    line_entries.append((vendor, norm.get("line_items") or []))
                    continue
                recs.append({
                    "invoice_number": str(norm.get("invoice_number") or ""),
                    "vendor_name": vendor,
                    "invoice_date": inv_date,
                    "total_amount": _num(norm.get("total_amount")),
                    "subtotal": _num(norm.get("subtotal")),
                    "tax": _num(norm.get("tax")),
                    "discount": _num(norm.get("discount")),
                    "payment_status": status,
                    "uploaded_at": str(inv.get("uploaded_at") or ""),
                })

            if item_q:
                # Line-item mode: per-vendor unit prices for the matching
                # product, computed in Python. Never merge vendors.
                return _json.dumps(_line_item_rates(line_entries, item_q))

            if sort == "uploaded_at_desc":
                # Newest first. Records without a timestamp ("") sort last
                # because "" compares smaller than any real timestamp string.
                recs.sort(key=lambda r: str(r.get("uploaded_at") or "").strip(), reverse=True)

            total = round(sum(r["total_amount"] for r in recs), 2)
            subtotal = round(sum(r["subtotal"] for r in recs), 2)
            tax = round(sum(r["tax"] for r in recs), 2)
            discount = round(sum(r["discount"] for r in recs), 2)
            count = len(recs)

            by_vendor = {}
            for r in recs:
                v = r["vendor_name"] or "Unknown"
                b = by_vendor.setdefault(v, {"count": 0, "total_amount": 0.0})
                b["count"] += 1
                b["total_amount"] = round(b["total_amount"] + r["total_amount"], 2)

            by_status = {}
            for r in recs:
                s = r["payment_status"] or "Unknown"
                b = by_status.setdefault(s, {"count": 0, "total_amount": 0.0})
                b["count"] += 1
                b["total_amount"] = round(b["total_amount"] + r["total_amount"], 2)

            result = {
                "count": count,
                "total_amount": total,
                "subtotal": subtotal,
                "tax": tax,
                "discount": discount,
                "average_total": round(total / count, 2) if count else 0.0,
                "by_vendor": by_vendor,
                "by_status": by_status,
            }
            if limit > 0:
                result["invoices"] = recs[:limit]
            if vendor_q and len(by_vendor) > 1:
                result["disambiguation"] = (
                    f"vendor filter '{vendor_q}' matched {len(by_vendor)} vendors: "
                    + ", ".join(sorted(by_vendor))
                    + ". Present each vendor's total separately and ask the user which one "
                    "they meant. Do NOT report the merged total as the answer."
                )
            return _json.dumps(result)

        return [answer_invoice_question, extract_data_ocr, generate_excel, generate_graph, invoice_aggregate]

    # ─────────────────────────── Public API ──────────────────────────

    def run(self, user_query: str, idempotency_key: Optional[str] = None) -> str:
        """
        Process a user message and return the assistant's response.

        Steps:
        1. Persist the user message to chat_store.
        2. Trigger summarisation if the threshold is reached.
        3. Build context block (summary + recent messages).
        4. Invoke the LangGraph ReAct agent with the context + current message.
        5. Persist the assistant response to chat_store.
        6. Return the final response text.

        Parameters
        ----------
        user_query : str
            The user's current message.
        idempotency_key : str, optional
            Caller-supplied deduplication token for the user message.
            If the same key is sent again (retry), the message is not saved twice.
        """
        # Scope every invoice-data read in this request to the owner
        from vector_store import set_current_user_id as _set_uid
        _set_uid(self.user_id)

        # 1. Save user message (with deduplication)
        save_message(
            conversation_id=self.conversation_id,
            role="user",
            content=user_query,
            idempotency_key=idempotency_key,
            db_path=self.db_path,
        )

        # 2. Summarise if needed
        try:
            maybe_summarise(
                conversation_id=self.conversation_id,
                summarise_threshold=self.summarise_threshold,
                recent_window=self.recent_window,
                db_path=self.db_path,
            )
        except Exception as exc:
            logger.warning("Summarisation step failed (non-fatal): %s", exc)

        # 3. Build context
        context = build_context_block(
            conversation_id=self.conversation_id,
            recent_window=self.recent_window,
            db_path=self.db_path,
        )

        # 4. Assemble messages list for the agent
        #    = [summary_system_msg (if any)] + [recent verbatim messages] + [current user msg]
        lc_messages = context["lc_messages"].copy()
        if not lc_messages or not (
            isinstance(lc_messages[-1], HumanMessage)
            and lc_messages[-1].content == user_query
        ):
            lc_messages.append(HumanMessage(content=user_query))

        # 5. Invoke the agent with a stable thread config
        thread_config = {
            "configurable": {
                "thread_id": self.thread_id,
            }
        }

        # Check if the checkpointer already has conversation history for this thread
        cp = _get_checkpointer()
        existing_cp = cp.get_tuple(thread_config)
        has_existing = bool(
            existing_cp
            and existing_cp.checkpoint.get("channel_values", {}).get("messages")
        )

        agent_input = (
            {"messages": [HumanMessage(content=user_query)]}
            if has_existing
            else {"messages": lc_messages}
        )

        try:
            response = self._agent.invoke(
                agent_input,
                config=thread_config,
            )
            final_response = response["messages"][-1].content
        except Exception as exc:
            logger.warning("Agent invocation failed for thread %s: %s. Initiating self-healing recovery...", self.thread_id, exc)
            if "429" in str(exc) or "rate limit" in str(exc).lower():
                import time
                time.sleep(8)
            # Self-healing recovery: clear internal thread checkpoints in SQLite and re-invoke with context block
            try:
                conn = cp._conn()
                conn.execute("DELETE FROM lg_checkpoints WHERE thread_id=?", (self.thread_id,))
                conn.execute("DELETE FROM lg_writes WHERE thread_id=?", (self.thread_id,))
                conn.execute("DELETE FROM lg_blobs WHERE thread_id=?", (self.thread_id,))
                conn.commit()
                response = self._agent.invoke(
                    {"messages": lc_messages},
                    config=thread_config,
                )
                final_response = response["messages"][-1].content
                logger.info("Self-healing recovery succeeded for thread %s", self.thread_id)
            except Exception as retry_exc:
                logger.error("Self-healing recovery failed for conversation %s: %s", self.conversation_id, retry_exc)
                err_text = str(exc).lower() + " " + str(retry_exc).lower()
                if "429" in err_text or "rate limit" in err_text or "tokens" in err_text:
                    final_response = "The AI service is busy, please wait a few seconds and try again."
                else:
                    final_response = "The AI service encountered a temporary error. Please try again."

        # 6. Save assistant response
        save_message(
            conversation_id=self.conversation_id,
            role="assistant",
            content=final_response,
            db_path=self.db_path,
        )

        return final_response

    # ─────────────────────────── Helpers ─────────────────────────────

    def get_history(self, limit: Optional[int] = None) -> list[dict]:
        """Return message history for this conversation (user-isolated)."""
        return get_messages(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            limit=limit,
            db_path=self.db_path,
        )

    @staticmethod
    def list_user_conversations(user_id: str, db_path: str = CHAT_DB_PATH) -> list[dict]:
        """List all conversations for a given user, most-recently-updated first."""
        return list_conversations(user_id=user_id, db_path=db_path)

    @property
    def graph_agent(self) -> _GraphAgent:
        """Expose the GraphAgent for Streamlit's direct figure rendering."""
        return self._graph_agent