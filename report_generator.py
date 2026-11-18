"""
report_generator.py - SQL-Based Excel Report Generator (Task 1 & Task 12).
Generates formatted Excel schedules directly from SQLite invoice records.
"""

import os
import datetime as dt
from typing import Optional, List, Dict, Any

import pandas as pd

from config import DEFAULT_INDEX_PATH
from vector_store import (
    get_all_invoices,
    search_invoices_fts,
    format_date_de,
)


class ReportAgent:
    """Excel Reporting Sub-Agent backed by SQLite."""

    def __init__(self, index_path: str = DEFAULT_INDEX_PATH, user_id: Optional[str] = None):
        self.index_path = index_path
        self.user_id = str(user_id) if user_id is not None else None

    def generate_report(self, query: str = "", output_path: str = "invoice_report.xlsx") -> dict:
        all_invoices = get_all_invoices(user_id=self.user_id)
        if not all_invoices:
            return {
                "status": "failed",
                "message": "Keine Rechnungen in der Datenbank gefunden. / No invoices found in storage.",
                "file_path": None,
                "record_count": 0,
                "columns": [],
                "error": "No invoices in SQLite",
            }

        q_low = (query or "").lower()
        filtered = all_invoices
        if "unpaid" in q_low or "offen" in q_low or "pending" in q_low:
            filtered = [
                inv for inv in all_invoices
                if str(inv.get("payment_status") or "").lower() not in ("paid", "bezahlt")
            ]
        elif "paid" in q_low or "bezahlt" in q_low:
            filtered = [
                inv for inv in all_invoices
                if str(inv.get("payment_status") or "").lower() in ("paid", "bezahlt")
            ]
        elif query.strip() and not any(w in q_low for w in ["all", "alle", "report", "excel", "export"]):
            fts = search_invoices_fts(query, user_id=self.user_id)
            if fts:
                filtered = fts

        if not filtered:
            filtered = all_invoices

        rows = []
        for inv in filtered:
            rows.append({
                "Rechnungs-Nr.": inv.get("invoice_number", "N/A"),
                "Belegart": "Gutschrift" if inv.get("document_type") == "credit_note" else "Rechnung",
                "Lieferant": inv.get("vendor_name", "Unknown"),
                "USt-IdNr.": inv.get("vat_id", ""),
                "Käufer": inv.get("buyer_name", "N/A"),
                "Belegdatum": format_date_de(inv.get("invoice_date")) or inv.get("invoice_date", "N/A"),
                "Fälligkeit": format_date_de(inv.get("due_date")) or inv.get("due_date", ""),
                "Netto (EUR)": float(inv.get("subtotal") or 0.0),
                "USt (EUR)": float(inv.get("tax_amount") or 0.0),
                "Rabatt (EUR)": float(inv.get("discount") or 0.0),
                "Brutto (EUR)": float(inv.get("total_amount") or 0.0),
                "Währung": inv.get("currency", "EUR"),
                "Status": inv.get("payment_status", "Unpaid"),
                "Prüfstatus": inv.get("validation_status", "PENDING"),
                "Leitweg-ID": inv.get("leitweg_id", ""),
            })

        try:
            out_dir = os.path.dirname(os.path.abspath(output_path))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            df = pd.DataFrame(rows)
            df.to_excel(output_path, index=False, engine="openpyxl")
            msg = f"Excel-Bericht mit {len(df)} Belegen wurde erstellt."
            return {
                "status": "success",
                "message": msg,
                "file_path": output_path,
                "record_count": len(df),
                "columns": list(df.columns),
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "message": f"Fehler beim Erstellen des Excel-Berichts: {str(exc)}",
                "file_path": None,
                "record_count": 0,
                "columns": [],
                "error": str(exc),
            }


def create_invoice_excel_report(
    index_path: str = DEFAULT_INDEX_PATH,
    period_text: str = "",
    output_path: str = "invoice_report.xlsx",
) -> str:
    agent = ReportAgent(index_path=index_path)
    res = agent.generate_report(query=period_text, output_path=output_path)
    return res["message"]


create_excel = create_invoice_excel_report
invoice_qa = create_invoice_excel_report