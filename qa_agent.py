"""
qa_agent.py - SQL-Only Invoice QA Agent (Task 1).

All invoice queries execute against the invoice database (`invoices` table + full-text
search on vendor_name, invoice_number, and item_descriptions).
"""

import re
import logging
from typing import Optional, List, Dict, Any

from config import GROQ_API_KEY, GROQ_MODEL_NAME, DEFAULT_INDEX_PATH
from vector_store import (
    get_all_invoices,
    search_invoices_fts,
    format_number_de,
    format_date_de,
)

logger = logging.getLogger(__name__)


def _cite_invoice(inv_num: str) -> str:
    """Format an invoice number as a clickable markdown citation link."""
    clean = str(inv_num or "N/A").strip()
    if not clean or clean == "N/A":
        return clean
    return f"[{clean}](#invoice/{clean})"


def _fmt_amt(val: Any, currency: str = "EUR") -> str:
    ccy = str(currency or "EUR").strip().upper()
    num_str = format_number_de(val, 2)
    return f"{num_str} €" if ccy == "EUR" else f"{num_str} {ccy}"


def execute_sql_invoice_search(
    question: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run SQL and full-text queries for a natural-language question.
    Returns matching invoices and structured field context.
    """
    all_invs = get_all_invoices(user_id=user_id)
    fts_matches = search_invoices_fts(question, user_id=user_id, limit=15)

    q_upper = question.upper()
    direct_matches = [
        inv for inv in all_invs
        if inv.get("invoice_number") and str(inv["invoice_number"]).upper() in q_upper
    ]

    target_invs = direct_matches or fts_matches or all_invs
    return {
        "total_stored": len(all_invs),
        "matched_count": len(target_invs),
        "fts_match_count": len(fts_matches),
        "invoices": target_invs[:15],
        "all_invoices": all_invs,
    }


def _format_sql_context_for_llm(sql_data: Dict[str, Any]) -> str:
    lines = [f"Invoices Count: {sql_data.get('total_stored', 0)}"]
    for inv in sql_data.get("invoices", []):
        inv_no = inv.get("invoice_number", "N/A")
        ccy = inv.get("currency", "EUR")
        lines.append(
            f"- Invoice {_cite_invoice(inv_no)} | Vendor: {inv.get('vendor_name')} | "
            f"Buyer: {inv.get('buyer_name')} | VAT ID: {inv.get('vat_id') or 'missing'} | "
            f"Date: {inv.get('invoice_date')} | Due: {inv.get('due_date') or 'N/A'} | "
            f"Subtotal: {inv.get('subtotal'):.2f} {ccy} | "
            f"VAT: {inv.get('tax_amount'):.2f} {ccy} | "
            f"Discount: {inv.get('discount'):.2f} | "
            f"Total: {inv.get('total_amount'):.2f} {ccy} | "
            f"Status: {inv.get('payment_status')} | Validation: {inv.get('validation_status')} | "
            f"Uploaded At: {inv.get('uploaded_at')}"
        )
        items = inv.get("line_items") or []
        for it in items[:15]:
            lines.append(
                f"    Pos {it.get('pos')}: [{it.get('article_number')}] {it.get('description')} | "
                f"Qty: {it.get('quantity')} {it.get('unit')} | "
                f"Price: {it.get('unit_price')} / PE {it.get('price_unit', 1)} | "
                f"Disc: {it.get('discount_percent', 0)}% | "
                f"Line Total: {it.get('line_total')} {ccy} | VAT: {it.get('vat_rate')}%"
            )
    return "\n".join(lines)


class QAAgent:
    """Invoice QA & Reasoning Sub-Agent for Orchestrator."""

    def __init__(self, index_path: str = DEFAULT_INDEX_PATH, user_id: Optional[str] = None):
        self.index_path = index_path
        self.user_id = str(user_id) if user_id is not None else None

    def answer(self, question: str, chat_history: Optional[list] = None, lang: str = "de") -> dict:
        """
        Execute database lookup and return structured answer with citations in the requested language.
        """
        is_en = str(lang or "de").strip().lower().startswith("en")
        try:
            sql_data = execute_sql_invoice_search(question, user_id=self.user_id)
            if sql_data["total_stored"] == 0:
                return {
                    "status": "failed",
                    "answer": (
                        "No invoices have been stored yet."
                        if is_en
                        else "Es sind noch keine Rechnungen gespeichert."
                    ),
                    "retrieval_mode": "sql",
                    "error": "No invoices stored",
                }

            sql_context = _format_sql_context_for_llm(sql_data)

            if GROQ_API_KEY:
                try:
                    from langchain_groq import ChatGroq
                    from langchain_core.prompts import ChatPromptTemplate

                    history_text = ""
                    if chat_history:
                        h_lines = []
                        for msg in chat_history[-6:]:
                            role = getattr(msg, "type", "User")
                            content = getattr(msg, "content", str(msg))
                            h_lines.append(f"{role.capitalize()}: {content}")
                        history_text = "\nPrevious Conversation:\n" + "\n".join(h_lines) + "\n"

                    persona = (
                        "You are an English accounting copilot. Answer in English."
                        if is_en
                        else "You are a German accounting copilot. Answer in German."
                    )

                    llm = ChatGroq(api_key=GROQ_API_KEY, model=GROQ_MODEL_NAME, temperature=0, max_tokens=1000)
                    prompt = ChatPromptTemplate.from_template(
                        """{persona}
Answer the user's question using ONLY the invoice records below.
Rules:
- Never compute sums, averages, or counts yourself; state only the exact figures from the records.
- Format every invoice reference as a markdown link like [RE-2026-10418](#invoice/RE-2026-10418).
- Format numbers as 1.234,56 with each invoice's own currency and dates as DD.MM.YYYY.
- Never mention internal tool or database names.
- Never use em dashes in your response; use standard hyphens (-) instead.

Invoice Records:
{sql_context}
{history}

User Question: "{query}"

Answer:"""
                    )
                    chain = prompt | llm
                    response = chain.invoke({
                        "persona": persona,
                        "sql_context": sql_context,
                        "history": history_text,
                        "query": question,
                    })
                    ans = getattr(response, "content", str(response)).strip().replace("\u2014", "-")
                    if ans:
                        return {
                            "status": "success",
                            "answer": ans,
                            "retrieval_mode": "sql",
                            "error": None,
                        }
                except Exception as llm_exc:
                    logger.debug("Groq synthesis unavailable in QAAgent, using deterministic answer: %s", llm_exc)

            invs = sql_data["invoices"]
            rows_out = [
                f"Matching invoices ({len(invs)} found):"
                if is_en
                else f"Gefundene Belege ({len(invs)} Treffer):"
            ]
            for inv in invs[:10]:
                inv_no = inv.get("invoice_number", "N/A")
                cite = _cite_invoice(inv_no)
                vendor = inv.get("vendor_name", "Unknown" if is_en else "Unbekannt")
                ccy = str(inv.get("currency") or "EUR").upper()
                dt_de = format_date_de(inv.get("invoice_date")) or inv.get("invoice_date", "N/A")
                due_de = format_date_de(inv.get("due_date")) or (inv.get("due_date") or ("none" if is_en else "keine"))
                sub_de = _fmt_amt(inv.get("subtotal", 0.0), ccy)
                tax_de = _fmt_amt(inv.get("tax_amount", 0.0), ccy)
                tot_de = _fmt_amt(inv.get("total_amount", 0.0), ccy)
                val_st = inv.get("validation_status", "PENDING")
                if is_en:
                    rows_out.append(
                        f"- **{cite}** ({vendor}, Date: {dt_de}, Due: {due_de}): "
                        f"Net {sub_de}, VAT {tax_de}, Gross **{tot_de}** "
                        f"(Status: {inv.get('payment_status')}, Validation: {val_st})"
                    )
                else:
                    rows_out.append(
                        f"- **{cite}** ({vendor}, Datum: {dt_de}, Fällig: {due_de}): "
                        f"Netto {sub_de}, USt {tax_de}, Brutto **{tot_de}** "
                        f"(Status: {inv.get('payment_status')}, Prüfung: {val_st})"
                    )

            return {
                "status": "success",
                "answer": "\n".join(rows_out),
                "retrieval_mode": "sql",
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "answer": (
                    f"Error querying invoices: {str(exc)}"
                    if is_en
                    else f"Fehler bei der Belegabfrage: {str(exc)}"
                ),
                "retrieval_mode": "sql",
                "error": str(exc),
            }


def invoice_qa(query: str, index_path: str = DEFAULT_INDEX_PATH, chat_history: list = None, lang: str = "de") -> str:
    agent = QAAgent(index_path=index_path)
    res = agent.answer(question=query, chat_history=chat_history, lang=lang)
    return res["answer"]


def query_invoice_data(index_path: str, question: str, chat_history: list = None, lang: str = "de") -> str:
    return invoice_qa(query=question, index_path=index_path, chat_history=chat_history, lang=lang)
