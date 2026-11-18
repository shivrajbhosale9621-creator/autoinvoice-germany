"""
qa_agent.py - SQL-Only Invoice QA Agent (Task 1).

ChromaDB vector search and LangGraph StateGraph have been removed.
All invoice queries execute against SQLite (`invoices` table + `invoices_fts` FTS5
full-text index on vendor_name, invoice_number, and item_descriptions).
"""

import json
import re
import sqlite3
import logging
from typing import Optional, List, Dict, Any

from config import GROQ_API_KEY, GROQ_MODEL_NAME, CHAT_DB_PATH, DEFAULT_INDEX_PATH
from vector_store import (
    get_all_invoices,
    search_invoices_fts,
    format_eur_de,
    format_date_de,
    _user_id_variants,
)

logger = logging.getLogger(__name__)


def _cite_invoice(inv_num: str) -> str:
    """Format an invoice number as a clickable markdown citation link (Tier 2)."""
    clean = str(inv_num or "N/A").strip()
    if not clean or clean == "N/A":
        return clean
    return f"[{clean}](#invoice/{clean})"


def execute_sql_invoice_search(
    question: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run SQL and FTS5 queries against SQLite for a natural-language question.
    Returns matching invoices, FTS5 matches, and structured field context.
    """
    all_invs = get_all_invoices(user_id=user_id)
    fts_matches = search_invoices_fts(question, user_id=user_id, limit=15)

    # Check if specific invoice number is mentioned in question
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
    lines = [f"SQLite Invoices Count: {sql_data.get('total_stored', 0)}"]
    for inv in sql_data.get("invoices", []):
        inv_no = inv.get("invoice_number", "N/A")
        lines.append(
            f"- Invoice {_cite_invoice(inv_no)} | Vendor: {inv.get('vendor_name')} | "
            f"Buyer: {inv.get('buyer_name')} | VAT ID: {inv.get('vat_id') or 'fehlt'} | "
            f"Date: {inv.get('invoice_date')} | Due: {inv.get('due_date') or 'N/A'} | "
            f"Subtotal (Netto): {inv.get('subtotal'):.2f} {inv.get('currency', 'EUR')} | "
            f"VAT (USt): {inv.get('tax_amount'):.2f} {inv.get('currency', 'EUR')} | "
            f"Discount: {inv.get('discount'):.2f} | "
            f"Total (Brutto): {inv.get('total_amount'):.2f} {inv.get('currency', 'EUR')} | "
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
                f"Line Total: {it.get('line_total')} EUR | VAT: {it.get('vat_rate')}%"
            )
    return "\n".join(lines)


class QAAgent:
    """SQL-Only QA & Reasoning Sub-Agent for Orchestrator."""

    def __init__(self, index_path: str = DEFAULT_INDEX_PATH, user_id: Optional[str] = None):
        self.index_path = index_path
        self.user_id = str(user_id) if user_id is not None else None

    def answer(self, question: str, chat_history: Optional[list] = None) -> dict:
        """
        Execute SQL + FTS5 lookup and return structured answer with citations.
        """
        try:
            sql_data = execute_sql_invoice_search(question, user_id=self.user_id)
            if sql_data["total_stored"] == 0:
                return {
                    "status": "failed",
                    "answer": "Es sind noch keine Rechnungen in der SQLite-Datenbank gespeichert. / No invoices have been stored yet.",
                    "retrieval_mode": "sql",
                    "error": "No invoices in SQLite",
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

                    llm = ChatGroq(api_key=GROQ_API_KEY, model=GROQ_MODEL_NAME, temperature=0, max_tokens=1000)
                    prompt = ChatPromptTemplate.from_template(
                        """You are an expert German Invoice SQL Assistant.
Answer the user's question using ONLY the SQLite query results below.
Rules:
- Never compute sums, averages, or counts yourself; state only the exact figures from the records.
- Format every invoice reference as a markdown link like [RE-2026-10418](#invoice/RE-2026-10418).
- Format EUR amounts in German notation (1.234,56 €) and dates as DD.MM.YYYY.
- Never use em dashes in your response; use standard hyphens (-) instead.

SQLite Records & FTS5 Matches:
{sql_context}
{history}

User Question: "{query}"

Answer:"""
                    )
                    chain = prompt | llm
                    response = chain.invoke({"sql_context": sql_context, "history": history_text, "query": question})
                    ans = getattr(response, "content", str(response)).strip().replace("-", "-")
                    if ans:
                        return {
                            "status": "success",
                            "answer": ans,
                            "retrieval_mode": "sql_fts5",
                            "error": None,
                        }
                except Exception as llm_exc:
                    logger.debug("Groq synthesis unavailable in QAAgent, using deterministic SQL answer: %s", llm_exc)

            # Deterministic SQL/FTS5 synthesis with citations
            invs = sql_data["invoices"]
            rows_out = [
                f"Gefundene Belege in SQLite ({len(invs)} Treffer):"
            ]
            for inv in invs[:10]:
                inv_no = inv.get("invoice_number", "N/A")
                cite = _cite_invoice(inv_no)
                vendor = inv.get("vendor_name", "Unbekannt")
                dt_de = format_date_de(inv.get("invoice_date")) or inv.get("invoice_date", "N/A")
                due_de = format_date_de(inv.get("due_date")) or inv.get("due_date", "N/A")
                sub_de = format_eur_de(inv.get("subtotal", 0.0))
                tax_de = format_eur_de(inv.get("tax_amount", 0.0))
                tot_de = format_eur_de(inv.get("total_amount", 0.0))
                val_st = inv.get("validation_status", "PENDING")
                rows_out.append(
                    f"- **{cite}** ({vendor}, Datum: {dt_de}, Fällig: {due_de}): "
                    f"Netto {sub_de}, USt {tax_de}, Brutto **{tot_de}** "
                    f"(Status: {inv.get('payment_status')}, Prüfung: {val_st})"
                )

            return {
                "status": "success",
                "answer": "\n".join(rows_out),
                "retrieval_mode": "sql_fts5",
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "answer": f"Fehler bei der SQL-Abfrage: {str(exc)}",
                "retrieval_mode": "sql",
                "error": str(exc),
            }


def invoice_qa(query: str, index_path: str = DEFAULT_INDEX_PATH, chat_history: list = None) -> str:
    agent = QAAgent(index_path=index_path)
    res = agent.answer(question=query, chat_history=chat_history)
    return res["answer"]


def query_invoice_data(index_path: str, question: str, chat_history: list = None) -> str:
    return invoice_qa(query=question, index_path=index_path, chat_history=chat_history)
