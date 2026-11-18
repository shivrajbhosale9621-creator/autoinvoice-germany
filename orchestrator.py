"""
orchestrator.py - Thin SQL Router & Copilot Orchestrator with Persistent Memory (Task 1).

Architecture (German Build):
----------------------------
1. Replaces LangGraph and ChromaDB vector search with a thin SQL router:
   - Classifies the user question (resolving follow-up context from conversation_memory).
   - Calls the appropriate Python/SQL tool (`invoice_aggregate`, `query_invoices_sql`,
     `get_validation_summary`, `extract_data_ocr`, `generate_excel`, `generate_graph`,
     `propose_copilot_action`).
   - Keeps `invoice_aggregate` as the ONLY place totals, sums, averages, and counts
     are computed. The LLM never computes numbers.
   - Formats answers with clickable invoice citations `[RE-2026-10418](#invoice/RE-2026-10418)`
     and supports human-in-the-loop approval cards for DATEV booking / marking paid.
"""

import os
import re
import json
import uuid
import sqlite3
import logging
import datetime
from typing import Optional, Dict, Any, List

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
from vector_store import (
    get_all_invoices,
    search_invoices_fts,
    normalize_invoice_data,
    set_current_user_id,
    format_eur_de,
    format_date_de,
    parse_numeric,
)
from qa_agent import QAAgent as _QAAgent
from ocr_engine import OCRAgent as _OCRAgent
from graph_agent import GraphAgent as _GraphAgent
from report_generator import ReportAgent as _ReportAgent

logger = logging.getLogger(__name__)


def _cite(inv_num: str) -> str:
    clean = str(inv_num or "N/A").strip()
    if not clean or clean == "N/A":
        return clean
    return f"[{clean}](#invoice/{clean})"


def _line_item_rates(entries: list, item_q: str) -> dict:
    """
    Aggregate matching line items by vendor in Python (Task 1 & Task 7).
    Computes exact unit prices and normalized price per 1 unit (unit_price / price_unit),
    and flags the cheapest vendor.
    """
    q = str(item_q or "").strip().lower()
    vendors: Dict[str, Any] = {}
    total_lines = 0
    invoices_with_match = 0

    def _fnum(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    for vendor, inv_num, items in entries:
        if not isinstance(items, list):
            continue
        matched_here = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            desc = str(
                it.get("description") or it.get("name") or it.get("desc")
                or it.get("item") or it.get("item_name") or it.get("product") or ""
            )
            art = str(it.get("article_number") or "")
            if q and (q not in desc.lower() and q not in art.lower()):
                continue
            price = it.get("unit_price")
            if price is None:
                price = it.get("rate")
            if price is None:
                price = it.get("price")
            price = _fnum(price)
            pe = _fnum(it.get("price_unit")) or 1.0
            if pe <= 0:
                pe = 1.0
            effective_unit_price = round(price / pe, 4) if price is not None else None
            if price is not None:
                price = round(price, 4)

            v = vendor or "Unknown"
            b = vendors.setdefault(v, {
                "unit_prices": [],
                "effective_unit_prices": [],
                "matching_lines": 0,
                "invoices": [],
                "sample_description": desc or art,
            })
            if price is not None and price not in b["unit_prices"]:
                b["unit_prices"].append(price)
                b["unit_prices"].sort()
            if effective_unit_price is not None and effective_unit_price not in b["effective_unit_prices"]:
                b["effective_unit_prices"].append(effective_unit_price)
                b["effective_unit_prices"].sort()
            if inv_num and inv_num not in b["invoices"]:
                b["invoices"].append(inv_num)
            b["matching_lines"] += 1
            matched_here += 1
            total_lines += 1
        if matched_here:
            invoices_with_match += 1

    # Flag cheapest vendor in Python
    cheapest_vendor = None
    cheapest_price = None
    for v_name, v_data in vendors.items():
        effs = v_data.get("effective_unit_prices") or v_data.get("unit_prices") or []
        if effs:
            min_p = min(effs)
            v_data["min_unit_price"] = min_p
            if cheapest_price is None or min_p < cheapest_price:
                cheapest_price = min_p
                cheapest_vendor = v_name

    for v_name, v_data in vendors.items():
        v_data["is_cheapest"] = (v_name == cheapest_vendor and cheapest_vendor is not None)

    return {
        "mode": "line_item",
        "line_item_query": str(item_q or "").strip(),
        "vendors": vendors,
        "cheapest_vendor": cheapest_vendor,
        "cheapest_unit_price": cheapest_price,
        "total_matching_lines": total_lines,
        "invoices_with_match": invoices_with_match,
    }


def compute_invoice_aggregate(
    filter_spec: Dict[str, Any],
    user_id: str,
) -> Dict[str, Any]:
    """
    Task 1: Exact Python/SQL aggregation over SQLite `invoices` table.
    The ONLY place totals, sums, averages, counts, and line-item rates are computed.
    """
    def _num(x):
        try:
            return float(x or 0.0)
        except (TypeError, ValueError):
            return 0.0

    f = filter_spec if isinstance(filter_spec, dict) else {}
    vendor_q = str(f.get("vendor") or "").strip().lower()
    exclude_vendor_q = str(f.get("exclude_vendor") or "").strip().lower()
    status_q = str(f.get("payment_status") or "").strip().lower()
    doc_type_q = str(f.get("document_type") or "").strip().lower()
    date_from = str(f.get("date_from") or "").strip()
    date_to = str(f.get("date_to") or "").strip()
    sort = str(f.get("sort") or "").strip()
    item_q = str(f.get("line_item") or "").strip()
    try:
        limit = int(f.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0

    recs = []
    line_entries = []

    all_invs = get_all_invoices(user_id=user_id)
    for inv in all_invs:
        vendor = str(inv.get("vendor_name") or "")
        status = str(inv.get("payment_status") or "Unpaid")
        inv_date = str(inv.get("invoice_date") or "")
        doc_type = str(inv.get("document_type") or "invoice")
        inv_num = str(inv.get("invoice_number") or "")

        if vendor_q and vendor_q not in vendor.lower():
            continue
        if exclude_vendor_q and exclude_vendor_q in vendor.lower():
            continue
        if status_q:
            if status_q in ("unpaid", "open", "offen", "pending"):
                if status.lower() == "paid" or status.lower() == "bezahlt":
                    continue
            elif status_q in ("paid", "bezahlt"):
                if status.lower() not in ("paid", "bezahlt"):
                    continue
            elif status_q != status.lower():
                continue
        if doc_type_q and doc_type_q != doc_type.lower():
            continue
        if date_from and inv_date < date_from:
            continue
        if date_to and inv_date > date_to:
            continue

        if item_q:
            line_entries.append((vendor, inv_num, inv.get("line_items") or []))
            continue

        recs.append({
            "id": inv.get("id"),
            "doc_id": inv.get("doc_id"),
            "invoice_number": inv_num,
            "citation": _cite(inv_num),
            "vendor_name": vendor,
            "buyer_name": str(inv.get("buyer_name") or ""),
            "vat_id": str(inv.get("vat_id") or ""),
            "document_type": doc_type,
            "invoice_date": inv_date,
            "due_date": str(inv.get("due_date") or ""),
            "total_amount": _num(inv.get("total_amount")),
            "subtotal": _num(inv.get("subtotal")),
            "tax": _num(inv.get("tax_amount")),
            "tax_amount": _num(inv.get("tax_amount")),
            "discount": _num(inv.get("discount")),
            "skonto_amount": _num(inv.get("skonto_amount")),
            "skonto_deadline": str(inv.get("skonto_deadline") or ""),
            "currency": str(inv.get("currency") or "EUR"),
            "payment_status": status,
            "validation_status": str(inv.get("validation_status") or "PENDING"),
            "uploaded_at": str(inv.get("uploaded_at") or ""),
        })

    if item_q:
        return _line_item_rates(line_entries, item_q)

    if sort == "uploaded_at_desc":
        recs.sort(key=lambda r: str(r.get("uploaded_at") or "").strip(), reverse=True)
    elif sort == "invoice_date_desc":
        recs.sort(key=lambda r: str(r.get("invoice_date") or "").strip(), reverse=True)
    elif sort == "amount_desc":
        recs.sort(key=lambda r: r.get("total_amount", 0.0), reverse=True)

    total = round(sum(r["total_amount"] for r in recs), 2)
    subtotal = round(sum(r["subtotal"] for r in recs), 2)
    tax = round(sum(r["tax_amount"] for r in recs), 2)
    discount = round(sum(r["discount"] for r in recs), 2)
    skonto_total = round(sum(r["skonto_amount"] for r in recs if r["payment_status"].lower() not in ("paid", "bezahlt")), 2)
    count = len(recs)

    by_vendor: Dict[str, Any] = {}
    for r in recs:
        v = r["vendor_name"] or "Unknown"
        b = by_vendor.setdefault(v, {"count": 0, "total_amount": 0.0, "subtotal": 0.0, "tax_amount": 0.0, "invoices": []})
        b["count"] += 1
        b["total_amount"] = round(b["total_amount"] + r["total_amount"], 2)
        b["subtotal"] = round(b["subtotal"] + r["subtotal"], 2)
        b["tax_amount"] = round(b["tax_amount"] + r["tax_amount"], 2)
        b["invoices"].append(r["invoice_number"])

    by_status: Dict[str, Any] = {}
    for r in recs:
        s = r["payment_status"] or "Unknown"
        b = by_status.setdefault(s, {"count": 0, "total_amount": 0.0, "invoices": []})
        b["count"] += 1
        b["total_amount"] = round(b["total_amount"] + r["total_amount"], 2)
        b["invoices"].append(r["invoice_number"])

    result: Dict[str, Any] = {
        "count": count,
        "total_amount": total,
        "subtotal": subtotal,
        "tax": tax,
        "tax_amount": tax,
        "discount": discount,
        "skonto_potential": skonto_total,
        "average_total": round(total / count, 2) if count else 0.0,
        "currency": "EUR",
        "by_vendor": by_vendor,
        "by_status": by_status,
        "invoices": recs[:limit] if limit > 0 else recs,
    }
    if vendor_q and len(by_vendor) > 1:
        result["disambiguation"] = (
            f"vendor filter '{vendor_q}' matched {len(by_vendor)} vendors: "
            + ", ".join(sorted(by_vendor))
            + ". Present each vendor's total separately and ask the user which one "
            "they meant. Do NOT report the merged total as the answer."
        )
    return result


def create_copilot_action_proposal(
    user_id: str,
    conversation_id: str,
    action_type: str,
    target_invoices: List[str],
    description: str,
    db_path: str = CHAT_DB_PATH,
) -> Dict[str, Any]:
    """
    Tier 2: Approval step.
    Store a proposed action (post_datev or mark_paid) in SQLite awaiting human confirmation.
    """
    init_db(db_path)
    action_id = f"act_{uuid.uuid4().hex[:10]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        conn.execute(
            """
            INSERT INTO copilot_actions (
                action_id, user_id, conversation_id, action_type,
                target_invoices_json, description, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                action_id,
                str(user_id),
                str(conversation_id),
                action_type,
                json.dumps(target_invoices),
                description,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "action_id": action_id,
        "action_type": action_type,
        "target_invoices": target_invoices,
        "description": description,
        "status": "pending",
    }


class MainOrchestrator:
    """
    Thin SQL Router Orchestrator with persistent conversation memory.
    Replaces LangGraph + ChromaDB with direct SQL/FTS5 routing and Python math.
    """

    def __init__(
        self,
        user_id: str = "default_user",
        conversation_id: Optional[str] = None,
        db_path: Optional[str] = None,
        summarise_threshold: int = SUMMARISE_THRESHOLD,
        recent_window: int = RECENT_WINDOW,
    ):
        self.user_id = str(user_id)
        self.db_path = db_path or CHAT_DB_PATH
        self.summarise_threshold = summarise_threshold
        self.recent_window = recent_window

        init_db(self.db_path)

        self.conversation_id = get_or_create_conversation(
            conversation_id=conversation_id or str(uuid.uuid4()),
            user_id=self.user_id,
            db_path=self.db_path,
        )
        self.thread_id = self.conversation_id

        self._qa_agent = _QAAgent(user_id=self.user_id)
        self._ocr_agent = _OCRAgent()
        self._graph_agent = _GraphAgent()
        self._graph_agent.user_id = self.user_id
        self._report_agent = _ReportAgent(user_id=self.user_id)

    def invoice_aggregate(self, filter_json: str) -> str:
        """Public tool method for exact Python/SQL aggregation."""
        try:
            f = json.loads(filter_json) if isinstance(filter_json, str) else dict(filter_json or {})
        except Exception:
            return json.dumps({"error": "filter_json must be valid JSON"})
        return json.dumps(compute_invoice_aggregate(f, user_id=self.user_id), ensure_ascii=False)

    # ─────────────────────────── Thin Router ─────────────────────────

    def _classify_intent(self, user_query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Thin router: classify the user query into one of:
          - extract_ocr (file attached)
          - propose_action (book to DATEV / mark paid -> requires human approval)
          - excel_report (generate Excel export)
          - graph (generate chart / plot)
          - validation (validation report / errors / checks V1-V9)
          - line_item_price (price comparison for a specific item)
          - aggregate (totals, sums, averages, counts, recency, vendor/status breakdowns)
          - sql_search (specific invoice details, fuzzy FTS5 search, Skonto, due dates)
        Uses conversation memory for follow-up context.
        """
        q = user_query.strip()
        q_low = q.lower()

        # 1. File attachment check
        m_file = re.search(r"\[Attached file saved at:\s*([^\]]+)\]", q)
        if m_file:
            file_path = m_file.group(1).strip()
            store_flag = not any(w in q_low for w in ["do not store", "don't store", "nicht speichern", "nur prüfen"])
            return {"route": "extract_ocr", "file_path": file_path, "store": store_flag}

        # Extract any invoice number mentioned in current query or recent history
        all_invs = get_all_invoices(user_id=self.user_id)
        known_inv_nums = [str(inv.get("invoice_number") or "") for inv in all_invs if inv.get("invoice_number")]
        mentioned_invs = [
            num for num in known_inv_nums
            if num.lower() in q_low
        ]
        # Follow-up resolution from recent messages if pronoun used ("diese", "it", "that invoice", "dafür")
        if not mentioned_invs and any(w in q_low for w in ["diese", "dieser", "diesen", "dafür", "this invoice", "that invoice", "those", "it", "sie"]):
            for msg in reversed(context.get("recent_messages") or []):
                c_text = str(msg.get("content") or "").lower()
                found_prev = [num for num in known_inv_nums if num.lower() in c_text]
                if found_prev:
                    mentioned_invs = found_prev
                    break

        # 2. Tier 2 Approval Step: Propose action (DATEV booking or Mark Paid)
        if any(w in q_low for w in [
            "mark paid", "mark as paid", "als bezahlt", "bezahlt markieren",
            "post to datev", "an datev", "datev buchen", "in datev", "verbuchen"
        ]):
            act_type = "post_datev" if "datev" in q_low or "verbuchen" in q_low else "mark_paid"
            targets = mentioned_invs or [inv["invoice_number"] for inv in all_invs if inv.get("payment_status", "").lower() != "paid"]
            return {"route": "propose_action", "action_type": act_type, "target_invoices": targets}

        # 3. Excel report
        if any(w in q_low for w in ["excel", ".xlsx", "spreadsheet", "export unpaid", "tabelle exportieren", "excel-bericht"]):
            return {"route": "excel_report", "query": q}

        # 4. Graph / Visualization
        if any(w in q_low for w in ["chart", "graph", "plot", "visual audit", "diagramm", "grafik", "visualisierung"]):
            return {"route": "graph", "query": q}

        # 5. Validation report / checks
        if any(w in q_low for w in [
            "validat", "prüf", "pruef", "fehler", "error", "check", "v1", "v2", "v3", "v4",
            "v5", "v6", "v7", "v8", "v9", "leitweg", "ustg", "warum fail", "abweichung"
        ]):
            return {"route": "validation", "target_invoices": mentioned_invs}

        # 6. Line-item price comparison
        m_item = re.search(
            r"(?:preis|rate|price|kosten|cost|vergleich|compare).*?(?:für|for|von|of)\s+([A-Za-z0-9ÄÖÜäöüß\-]{3,40})",
            q,
            flags=re.IGNORECASE,
        )
        if m_item or any(w in q_low for w in ["preisvergleich", "price comparison", "unit price", "einzelpreis", "welcher lieferant ist am günstigsten"]):
            item_term = m_item.group(1).strip(" ?.!,'\"") if m_item else ""
            # Also check if any known article number or product word is in the query
            if not item_term:
                for inv in all_invs:
                    for it in inv.get("line_items") or []:
                        art = str(it.get("article_number") or "")
                        desc_words = [w for w in re.findall(r"[\wÄÖÜäöüß-]{4,}", str(it.get("description") or ""))]
                        if art and art.lower() in q_low:
                            item_term = art
                            break
                        for dw in desc_words:
                            if dw.lower() in q_low and dw.lower() not in ("preis", "stück", "rechnung"):
                                item_term = dw
                                break
                        if item_term:
                            break
                    if item_term:
                        break
            return {"route": "aggregate", "filter": {"line_item": item_term or q}}

        # 7. Recency / Aggregate / Totals / Counts / Averages
        if any(w in q_low for w in [
            "last uploaded", "most recent", "zuletzt hochgeladen", "neueste", "letzte rechnung"
        ]):
            return {"route": "aggregate", "filter": {"sort": "uploaded_at_desc", "limit": 5}}

        if any(w in q_low for w in [
            "how many", "wie viele", "count", "anzahl", "total", "summe", "gesamt",
            "spend", "umsatz", "ausgaben", "average", "durchschnitt", "top", "rank",
            "unpaid", "offen", "paid", "bezahlt", "skonto", "cashflow", "fällig", "due"
        ]) and not mentioned_invs:
            flt: Dict[str, Any] = {}
            if "unpaid" in q_low or "offen" in q_low:
                flt["payment_status"] = "unpaid"
            elif "paid" in q_low or "bezahlt" in q_low:
                flt["payment_status"] = "paid"
            return {"route": "aggregate", "filter": flt}

        # 8. Specific invoice or FTS5 search
        return {"route": "sql_search", "query": q, "target_invoices": mentioned_invs}

    def _format_validation_summary(self, target_invoices: List[str]) -> str:
        all_invs = get_all_invoices(user_id=self.user_id)
        if not all_invs:
            return "Es sind aktuell keine Rechnungen zur Prüfung vorhanden."

        invs = [
            inv for inv in all_invs
            if not target_invoices or inv.get("invoice_number") in target_invoices
        ]
        if not invs:
            invs = all_invs

        lines = ["### Prüfbericht-Zusammenfassung (9-Stufen-Validierung in Python)\n"]
        for inv in invs:
            inv_num = inv.get("invoice_number", "N/A")
            cite = _cite(inv_num)
            vr = inv.get("validation_report") or {}
            overall = vr.get("overall_status") or inv.get("validation_status") or "PENDING"
            xrechnung_ready = vr.get("xrechnung_ready", False)
            checks = vr.get("checks") or []
            failed_checks = [c for c in checks if c.get("status") == "FAIL"]
            unverified_checks = [c for c in checks if c.get("status") == "CANNOT VERIFY"]

            lines.append(
                f"- **{cite}** ({inv.get('vendor_name')}, Brutto: {format_eur_de(inv.get('total_amount'))}): "
                f"Status **{overall}** | XRechnung-fähig: **{'JA' if xrechnung_ready else 'NEIN'}**"
            )
            for fc in failed_checks:
                lines.append(f"  - **[FAIL] {fc.get('code')} ({fc.get('name')})**: {fc.get('message')}")
                for d in fc.get("discrepancies") or []:
                    lines.append(
                        f"    - Position {d.get('line', '-')}: Erwartet `{format_number_de_val(d.get('expected'))}`, "
                        f"Gedruckt `{format_number_de_val(d.get('printed'))}`, "
                        f"Differenz `{format_number_de_val(d.get('difference'))}`"
                    )
            for uc in unverified_checks:
                lines.append(f"  - **[CANNOT VERIFY] {uc.get('code')} ({uc.get('name')})**: {uc.get('message')}")
            if not failed_checks and not unverified_checks:
                lines.append("  - Alle 9 Prüfungen (V1 bis V9) erfolgreich bestanden (PASS).")

        return "\n".join(lines)

    def _phrase_aggregate_answer(self, user_query: str, agg_data: Dict[str, Any], context: Dict[str, Any]) -> str:
        """
        Phrase the answer from exact `invoice_aggregate` output.
        Never computes numbers in the LLM; every number comes from `agg_data` and carries a citation.
        """
        if agg_data.get("mode") == "line_item":
            q_item = agg_data.get("line_item_query") or "Artikel"
            vendors = agg_data.get("vendors") or {}
            if not vendors:
                return f"Für den Suchbegriff `{q_item}` wurden keine passenden Rechnungspositionen in SQLite gefunden."
            lines = [f"### Preisvergleich für `{q_item}` (aus SQLite berechnet)\n"]
            for v_name, v_info in vendors.items():
                prices_str = ", ".join(format_eur_de(p) for p in (v_info.get("unit_prices") or []))
                eff_str = ", ".join(f"{format_eur_de(p)} / Einh." for p in (v_info.get("effective_unit_prices") or []))
                invs_cites = ", ".join(_cite(i) for i in (v_info.get("invoices") or []))
                cheap_badge = " **[GÜNSTIGSTER ANBIETER / CHEAPEST]**" if v_info.get("is_cheapest") else ""
                lines.append(
                    f"- **{v_name}**{cheap_badge}: Einzelpreis {prices_str} "
                    f"(Effektivpreis je Einheit: {eff_str}) | Positionen: {v_info.get('matching_lines')} | "
                    f"Belege: {invs_cites}"
                )
            return "\n".join(lines)

        count = agg_data.get("count", 0)
        if count == 0:
            return "Es wurden keine Rechnungen gefunden, die den Filterkriterien entsprechen (Anzahl: 0, Summe: 0,00 €)."

        tot_de = format_eur_de(agg_data.get("total_amount", 0.0))
        sub_de = format_eur_de(agg_data.get("subtotal", 0.0))
        tax_de = format_eur_de(agg_data.get("tax_amount", 0.0))
        avg_de = format_eur_de(agg_data.get("average_total", 0.0))
        skonto_de = format_eur_de(agg_data.get("skonto_potential", 0.0))

        lines = [
            f"### Exakte Auswertung aus SQLite (`invoice_aggregate`)\n",
            f"- **Anzahl Belege**: {count}",
            f"- **Gesamtbetrag (Brutto)**: **{tot_de}**",
            f"- **Nettobetrag (Subtotal)**: {sub_de}",
            f"- **Umsatzsteuer (USt gesamt)**: {tax_de}",
            f"- **Durchschnittlicher Belegwert**: {avg_de}",
        ]
        if agg_data.get("skonto_potential", 0.0) > 0:
            lines.append(f"- **Verfügbares Skonto-Potenzial**: {skonto_de}")

        if agg_data.get("disambiguation"):
            lines.append(f"\nHinweis: {agg_data['disambiguation']}")

        by_vendor = agg_data.get("by_vendor") or {}
        if by_vendor:
            lines.append("\n**Aufschlüsselung nach Lieferant:**")
            for v_name, v_stat in sorted(by_vendor.items(), key=lambda x: x[1]["total_amount"], reverse=True):
                cites = ", ".join(_cite(i) for i in v_stat.get("invoices", []))
                lines.append(
                    f"- **{v_name}**: {v_stat['count']} Beleg(e), Brutto **{format_eur_de(v_stat['total_amount'])}** "
                    f"(Netto {format_eur_de(v_stat['subtotal'])}, USt {format_eur_de(v_stat['tax_amount'])}) "
                    f"| Quellen: {cites}"
                )

        invs = agg_data.get("invoices") or []
        if invs:
            lines.append("\n**Einzelbelege (Quellen):**")
            for r in invs[:10]:
                dt_de = format_date_de(r.get("invoice_date")) or r.get("invoice_date", "N/A")
                due_de = format_date_de(r.get("due_date")) or r.get("due_date", "N/A")
                lines.append(
                    f"- **{_cite(r['invoice_number'])}** | {r['vendor_name']} | Datum: {dt_de} | "
                    f"Fällig: {due_de} | Netto: {format_eur_de(r['subtotal'])} | "
                    f"USt: {format_eur_de(r['tax_amount'])} | Brutto: **{format_eur_de(r['total_amount'])}** | "
                    f"Status: {r['payment_status']}"
                )

        # If Groq is available, allow optional natural-language lead-in while preserving exact figures & citations
        if GROQ_API_KEY:
            try:
                from langchain_groq import ChatGroq
                from langchain_core.prompts import ChatPromptTemplate

                llm = ChatGroq(model=GROQ_MODEL_NAME, api_key=GROQ_API_KEY, temperature=0, max_tokens=600)
                prompt = ChatPromptTemplate.from_template(
                    """You are a German accounting copilot.
Write a short, direct 2-sentence answer to the user's question using ONLY the exact pre-computed figures below.
Never compute any math yourself, never change any numbers, and never use em dashes.
Always include clickable invoice links like [RE-2026-10418](#invoice/RE-2026-10418).

User Question: "{query}"
Pre-computed Tool Result:
{tool_text}

Concise Lead-in:"""
                )
                resp = (prompt | llm).invoke({"query": user_query, "tool_text": "\n".join(lines)})
                lead = getattr(resp, "content", "").strip().replace("-", "-")
                if lead:
                    return f"{lead}\n\n" + "\n".join(lines)
            except Exception:
                pass

        return "\n".join(lines)

    def run(self, user_query: str, idempotency_key: Optional[str] = None) -> str:
        """
        Process a user message through the thin SQL router and return the assistant response.
        """
        set_current_user_id(self.user_id)

        # Check idempotency: if message with same idempotency_key already exists, return the cached assistant reply
        if idempotency_key:
            existing_msgs = get_messages(self.conversation_id, self.user_id, db_path=self.db_path)
            for idx, m in enumerate(existing_msgs):
                if m.get("idempotency_key") == idempotency_key:
                    for follow in existing_msgs[idx + 1:]:
                        if follow.get("role") == "assistant":
                            return follow.get("content", "")

        # 1. Save user message
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

        # 3. Build conversation context for follow-up questions
        context = build_context_block(
            conversation_id=self.conversation_id,
            recent_window=self.recent_window,
            db_path=self.db_path,
        )

        # 4. Route and execute tool
        intent = self._classify_intent(user_query, context)
        route = intent.get("route", "sql_search")

        try:
            if route == "extract_ocr":
                file_path = intent["file_path"]
                store = intent.get("store", True)
                res = (
                    self._ocr_agent.process_and_index_invoice(file_path, user_id=self.user_id)
                    if store
                    else self._ocr_agent.extract_only(file_path)
                )
                ext = res.get("extracted_data") or {}
                inv_no = ext.get("invoice_number") or "N/A"
                cite = _cite(inv_no) if store else inv_no
                final_response = (
                    f"Beleg **{cite}** (`{res.get('file_name')}`) wurde extrahiert"
                    + (" und in SQLite gespeichert.\n\n" if store else " (nur Vorschau, nicht gespeichert).\n\n")
                    + f"- **Lieferant**: {ext.get('vendor_name', 'N/A')} (USt-IdNr.: {ext.get('vat_id') or 'fehlt'})\n"
                    + f"- **Rechnungsdatum**: {format_date_de(ext.get('invoice_date')) or ext.get('invoice_date', 'N/A')}\n"
                    + f"- **Fälligkeitsdatum**: {format_date_de(ext.get('due_date')) or ext.get('due_date', 'N/A')}\n"
                    + f"- **Nettobetrag**: {format_eur_de(ext.get('subtotal', 0.0))}\n"
                    + f"- **Umsatzsteuer**: {format_eur_de(ext.get('tax_amount', 0.0))}\n"
                    + f"- **Bruttobetrag**: **{format_eur_de(ext.get('total_amount', 0.0))}**\n"
                    + f"- **Prüfstatus**: {ext.get('validation_status', 'GEPRÜFT')}"
                )

            elif route == "propose_action":
                act_type = intent["action_type"]
                targets = intent.get("target_invoices") or []
                if not targets:
                    final_response = "Es wurden keine passenden Belege für diese Aktion gefunden."
                else:
                    label_de = (
                        "Buchungsübergabe an DATEV (SKR03/SKR04)"
                        if act_type == "post_datev"
                        else "Statusänderung auf BEZAHLT (Paid)"
                    )
                    cites = ", ".join(_cite(t) for t in targets)
                    desc = f"{label_de} für Beleg(e): {', '.join(targets)}"
                    proposal = create_copilot_action_proposal(
                        user_id=self.user_id,
                        conversation_id=self.conversation_id,
                        action_type=act_type,
                        target_invoices=targets,
                        description=desc,
                        db_path=self.db_path,
                    )
                    final_response = (
                        f"### Freigabe erforderlich: {label_de}\n\n"
                        f"Ich habe folgende Aktion für {cites} vorbereitet:\n"
                        f"- **Aktion**: {label_de}\n"
                        f"- **Betroffene Belege**: {cites}\n\n"
                        f"Bitte bestätigen Sie die Ausführung über die Freigabeschaltfläche unten. "
                        f"Ohne menschliche Freigabe wird keine Änderung durchgeführt.\n\n"
                        f"[ACTION_PROPOSAL: {proposal['action_id']}]"
                    )

            elif route == "excel_report":
                _ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                _uid = str(uuid.uuid4())[:8]
                _filename = f"invoice_report_{_ts}_{_uid}.xlsx"
                _reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
                os.makedirs(_reports_dir, exist_ok=True)
                _output_path = os.path.join(_reports_dir, _filename)
                res = self._report_agent.generate_report(user_query, output_path=_output_path)
                if res.get("status") == "success":
                    final_response = f"{res.get('message')} Download filename: {_filename}"
                else:
                    final_response = f"Excel-Export konnte nicht erstellt werden: {res.get('message')}"

            elif route == "graph":
                res = self._graph_agent.run(user_query)
                chart_paths = res.get("chart_paths") or []
                tags = "".join(f"\n[CHART: {cp}]" for cp in chart_paths)
                final_response = f"{res.get('message', 'Visualisierung erstellt.')}{tags}"

            elif route == "validation":
                final_response = self._format_validation_summary(intent.get("target_invoices") or [])

            elif route == "aggregate":
                agg_data = compute_invoice_aggregate(intent.get("filter") or {}, user_id=self.user_id)
                final_response = self._phrase_aggregate_answer(user_query, agg_data, context)

            else:
                # SQL + FTS5 Search combined with exact aggregate summary
                qa_res = self._qa_agent.answer(user_query, chat_history=context.get("lc_messages"))
                final_response = qa_res.get("answer", "")

        except Exception as exc:
            logger.error("Orchestrator routing error: %s", exc, exc_info=True)
            final_response = f"Bei der Verarbeitung der Anfrage ist ein Fehler aufgetreten: {str(exc)}"

        # Guardrail: Never allow em dashes in UI text or messages
        final_response = final_response.replace("-", "-")

        # 5. Save assistant response
        save_message(
            conversation_id=self.conversation_id,
            role="assistant",
            content=final_response,
            db_path=self.db_path,
        )
        return final_response

    def get_history(self, limit: Optional[int] = None) -> list[dict]:
        return get_messages(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            limit=limit,
            db_path=self.db_path,
        )

    @staticmethod
    def list_user_conversations(user_id: str, db_path: str = CHAT_DB_PATH) -> list[dict]:
        return list_conversations(user_id=user_id, db_path=db_path)

    @property
    def graph_agent(self) -> _GraphAgent:
        return self._graph_agent


def format_number_de_val(val: Any) -> str:
    if val is None or val == "":
        return "-"
    if isinstance(val, (int, float)):
        return format_eur_de(val)
    try:
        return format_eur_de(float(val))
    except Exception:
        return str(val)