"""
orchestrator.py - Thin SQL Router & Python Money-Math Orchestrator (Tasks 1, 3-6, Tier 2).

Replaces the LangGraph orchestrator with a thin, deterministic router:
1. Classifies the question (OCR extraction, action proposal, Excel report, chart,
   validation report, line-item price comparison, aggregate/recency/exclusion, or search).
2. Calls the right tool (`compute_invoice_aggregate` is the ONLY place totals, sums,
   averages, counts, days_until_due, and currency groupings are computed in Python).
3. Phrases the answer in the requested UI language (`de` or `en`) using locale formatting
   (EUR / foreign currency, 1.234,56, DD.MM.YYYY), with clickable invoice citations and
   zero internal tool/database names.
"""

import os
import re
import json
import uuid
import sqlite3
import logging
import datetime
from typing import Optional, List, Dict, Any

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
from ocr_engine import OCRAgent as _OCRAgent
from qa_agent import QAAgent as _QAAgent
from report_generator import ReportAgent as _ReportAgent
from graph_agent import GraphAgent as _GraphAgent
from vector_store import (
    get_all_invoices,
    set_current_user_id,
    parse_numeric,
    parse_german_date,
    format_eur_de,
    format_number_de,
    format_date_de,
)

logger = logging.getLogger(__name__)


def _cite(inv_num: str) -> str:
    clean = str(inv_num or "N/A").strip()
    if not clean or clean == "N/A":
        return clean
    return f"[{clean}](#invoice/{clean})"


def _fmt_money_ccy(val: Any, currency: str = "EUR") -> str:
    ccy = str(currency or "EUR").strip().upper()
    num_str = format_number_de(val, 2)
    return f"{num_str} €" if ccy == "EUR" else f"{num_str} {ccy}"


def _format_days_remaining(days_until_due: Optional[int], lang: str = "de") -> str:
    is_en = (lang == "en")
    if days_until_due is None:
        return "no due date" if is_en else "keine Fälligkeit"
    if days_until_due > 0:
        if is_en:
            unit = "day" if days_until_due == 1 else "days"
            return f"due in {days_until_due} {unit}"
        unit = "Tag" if days_until_due == 1 else "Tagen"
        return f"fällig in {days_until_due} {unit}"
    if days_until_due == 0:
        return "due today" if is_en else "heute fällig"
    overdue = abs(days_until_due)
    if is_en:
        unit = "day" if overdue == 1 else "days"
        return f"overdue by {overdue} {unit}"
    unit = "Tag" if overdue == 1 else "Tagen"
    return f"{overdue} {unit} überfällig"


def _line_item_rates(entries: List[tuple], item_q: str) -> Dict[str, Any]:
    """
    Task 7: Exact Python line-item price comparison across vendors.
    Computes unit prices, effective prices (unit_price / price_unit), and flags cheapest vendor.
    """
    q_low = str(item_q or "").strip().lower()
    tokens = [t for t in re.findall(r"[a-z0-9äöüß]+", q_low) if len(t) >= 2]
    vendors: Dict[str, Dict[str, Any]] = {}
    total_lines = 0
    invoices_with_match: List[str] = []

    for vendor, inv_num, items in entries:
        v_key = vendor or "Unknown"
        for it in items:
            if not isinstance(it, dict):
                continue
            desc = str(it.get("description") or it.get("name") or "").strip()
            art = str(it.get("article_number") or "").strip()
            hay = f"{art} {desc}".lower()
            if tokens and not all(t in hay for t in tokens):
                continue

            total_lines += 1
            if inv_num and inv_num not in invoices_with_match:
                invoices_with_match.append(inv_num)

            up = parse_numeric(it.get("unit_price"), 0.0)
            pe = parse_numeric(it.get("price_unit"), 1.0)
            if pe <= 0:
                pe = 1.0
            eff = round(up / pe, 4)
            qty = parse_numeric(it.get("quantity"), 0.0)
            lt = parse_numeric(it.get("line_total"), 0.0)

            b = vendors.setdefault(v_key, {
                "matching_lines": 0,
                "unit_prices": [],
                "effective_unit_prices": [],
                "total_quantity": 0.0,
                "total_spend": 0.0,
                "invoices": [],
            })
            b["matching_lines"] += 1
            if up not in b["unit_prices"]:
                b["unit_prices"].append(up)
            if eff not in b["effective_unit_prices"]:
                b["effective_unit_prices"].append(eff)
            b["total_quantity"] = round(b["total_quantity"] + qty, 2)
            b["total_spend"] = round(b["total_spend"] + lt, 2)
            if inv_num and inv_num not in b["invoices"]:
                b["invoices"].append(inv_num)

    cheapest_vendor = None
    cheapest_price = None
    for v_name, v_data in vendors.items():
        pos_eff = [p for p in v_data["effective_unit_prices"] if p > 0]
        if pos_eff:
            min_p = min(pos_eff)
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
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """
    Exact Python aggregation over stored invoices.
    The ONLY place totals, sums, averages, counts, days_until_due, and currency groupings
    are computed.
    """
    def _num(x):
        try:
            return float(x or 0.0)
        except (TypeError, ValueError):
            return 0.0

    today = reference_date or datetime.date.today()

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
    try:
        exclude_last = int(f.get("exclude_last") or 0)
    except (TypeError, ValueError):
        exclude_last = 0

    recs = []
    line_entries = []

    all_invs = get_all_invoices(user_id=user_id)
    for inv in all_invs:
        vendor = str(inv.get("vendor_name") or "")
        status = str(inv.get("payment_status") or "Unpaid")
        inv_date = str(inv.get("invoice_date") or "")
        doc_type = str(inv.get("document_type") or "invoice")
        inv_num = str(inv.get("invoice_number") or "")
        due_raw = str(inv.get("due_date") or "").strip()
        due_iso = parse_german_date(due_raw) if due_raw else ""
        days_until_due: Optional[int] = None
        if due_iso and re.match(r"^\d{4}-\d{2}-\d{2}$", due_iso):
            try:
                due_dt = datetime.datetime.strptime(due_iso, "%Y-%m-%d").date()
                days_until_due = (due_dt - today).days
            except ValueError:
                days_until_due = None

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

        ccy = str(inv.get("currency") or "EUR").strip().upper() or "EUR"
        src_ccy = str(inv.get("source_currency") or ccy).strip().upper() or ccy

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
            "due_date": due_iso or due_raw,
            "days_until_due": days_until_due,
            "total_amount": _num(inv.get("total_amount")),
            "subtotal": _num(inv.get("subtotal")),
            "tax": _num(inv.get("tax_amount")),
            "tax_amount": _num(inv.get("tax_amount")),
            "discount": _num(inv.get("discount")),
            "skonto_amount": _num(inv.get("skonto_amount")),
            "skonto_deadline": str(inv.get("skonto_deadline") or ""),
            "currency": ccy,
            "source_currency": src_ccy,
            "payment_status": status,
            "validation_status": str(inv.get("validation_status") or "PENDING"),
            "uploaded_at": str(inv.get("uploaded_at") or ""),
        })

    if item_q:
        return _line_item_rates(line_entries, item_q)

    # Sort BEFORE applying exclude_last and limit
    if sort == "uploaded_at_desc" or exclude_last > 0:
        recs.sort(key=lambda r: (str(r.get("uploaded_at") or "").strip(), int(r.get("id") or 0)), reverse=True)
    elif sort == "invoice_date_desc":
        recs.sort(key=lambda r: str(r.get("invoice_date") or "").strip(), reverse=True)
    elif sort == "amount_desc":
        recs.sort(key=lambda r: r.get("total_amount", 0.0), reverse=True)

    available_before_limit = len(recs)

    # Task 4: Apply exclude_last then limit BEFORE computing totals
    if exclude_last > 0:
        recs = recs[exclude_last:]
    if limit > 0:
        recs = recs[:limit]

    count = len(recs)

    # Task 6: Group totals by currency; never sum across different currencies into EUR
    by_currency: Dict[str, Dict[str, Any]] = {}
    for r in recs:
        c = r["currency"]
        cb = by_currency.setdefault(c, {
            "currency": c,
            "count": 0,
            "total_amount": 0.0,
            "subtotal": 0.0,
            "tax_amount": 0.0,
            "invoices": [],
        })
        cb["count"] += 1
        cb["total_amount"] = round(cb["total_amount"] + r["total_amount"], 2)
        cb["subtotal"] = round(cb["subtotal"] + r["subtotal"], 2)
        cb["tax_amount"] = round(cb["tax_amount"] + r["tax_amount"], 2)
        cb["invoices"].append(r["invoice_number"])

    currencies_present = list(by_currency.keys())
    if len(currencies_present) == 0:
        agg_currency = "EUR"
        total = 0.0
        subtotal = 0.0
        tax = 0.0
        discount = 0.0
        skonto_total = 0.0
    elif len(currencies_present) == 1:
        agg_currency = currencies_present[0]
        total = round(sum(r["total_amount"] for r in recs), 2)
        subtotal = round(sum(r["subtotal"] for r in recs), 2)
        tax = round(sum(r["tax_amount"] for r in recs), 2)
        discount = round(sum(r["discount"] for r in recs), 2)
        skonto_total = round(sum(r["skonto_amount"] for r in recs if r["payment_status"].lower() not in ("paid", "bezahlt")), 2)
    else:
        agg_currency = "MIXED"
        total = 0.0
        subtotal = 0.0
        tax = 0.0
        discount = 0.0
        skonto_total = 0.0

    unconverted_foreign = [
        r["invoice_number"] for r in recs
        if r["currency"] != "EUR"
    ]

    by_vendor: Dict[str, Any] = {}
    for r in recs:
        v = r["vendor_name"] or "Unknown"
        b = by_vendor.setdefault(v, {
            "count": 0,
            "total_amount": 0.0,
            "subtotal": 0.0,
            "tax_amount": 0.0,
            "currency": r["currency"],
            "currencies": set(),
            "invoices": [],
        })
        b["count"] += 1
        b["total_amount"] = round(b["total_amount"] + r["total_amount"], 2)
        b["subtotal"] = round(b["subtotal"] + r["subtotal"], 2)
        b["tax_amount"] = round(b["tax_amount"] + r["tax_amount"], 2)
        b["currencies"].add(r["currency"])
        b["invoices"].append(r["invoice_number"])
    for b in by_vendor.values():
        b["currencies"] = sorted(b["currencies"])
        if len(b["currencies"]) > 1:
            b["currency"] = "MIXED"

    by_status: Dict[str, Any] = {}
    for r in recs:
        s = r["payment_status"] or "Unknown"
        b = by_status.setdefault(s, {"count": 0, "total_amount": 0.0, "invoices": []})
        b["count"] += 1
        b["total_amount"] = round(b["total_amount"] + r["total_amount"], 2)
        b["invoices"].append(r["invoice_number"])

    result: Dict[str, Any] = {
        "count": count,
        "requested_limit": limit,
        "exclude_last": exclude_last,
        "available_before_limit": available_before_limit,
        "total_amount": total,
        "subtotal": subtotal,
        "tax": tax,
        "tax_amount": tax,
        "discount": discount,
        "skonto_potential": skonto_total,
        "average_total": round(total / count, 2) if (count and agg_currency != "MIXED") else 0.0,
        "currency": agg_currency,
        "by_currency": by_currency,
        "unconverted_foreign": unconverted_foreign,
        "by_vendor": by_vendor,
        "by_status": by_status,
        "invoices": recs,
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
    Store a proposed action (post_datev or mark_paid) awaiting human confirmation.
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
    Thin Router Orchestrator with persistent conversation memory.
    """

    def __init__(
        self,
        user_id: str = "default_user",
        conversation_id: Optional[str] = None,
        db_path: Optional[str] = None,
        summarise_threshold: int = SUMMARISE_THRESHOLD,
        recent_window: int = RECENT_WINDOW,
        lang: str = "de",
    ):
        self.user_id = str(user_id)
        self.db_path = db_path or CHAT_DB_PATH
        self.summarise_threshold = summarise_threshold
        self.recent_window = recent_window
        self.lang = "en" if str(lang or "de").strip().lower().startswith("en") else "de"

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
        """Public tool method for exact Python aggregation."""
        try:
            f = json.loads(filter_json) if isinstance(filter_json, str) else dict(filter_json or {})
        except Exception:
            return json.dumps({"error": "filter_json must be valid JSON"})
        return json.dumps(compute_invoice_aggregate(f, user_id=self.user_id), ensure_ascii=False)

    # ─────────────────────────── Thin Router ─────────────────────────

    def _classify_intent(self, user_query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        q = user_query.strip()
        q_low = q.lower()

        # 1. File attachment check
        m_file = re.search(r"\[Attached file saved at:\s*([^\]]+)\]", q)
        if m_file:
            file_path = m_file.group(1).strip()
            store_flag = not any(w in q_low for w in ["do not store", "don't store", "nicht speichern", "nur prüfen"])
            return {"route": "extract_ocr", "file_path": file_path, "store": store_flag}

        all_invs = get_all_invoices(user_id=self.user_id)
        known_inv_nums = [str(inv.get("invoice_number") or "") for inv in all_invs if inv.get("invoice_number")]
        mentioned_invs = [
            num for num in known_inv_nums
            if num.lower() in q_low
        ]
        if not mentioned_invs and any(w in q_low for w in ["diese", "dieser", "diesen", "dafür", "this invoice", "that invoice", "those", "it", "sie"]):
            for msg in reversed(context.get("recent_messages") or []):
                c_text = str(msg.get("content") or "").lower()
                found_prev = [num for num in known_inv_nums if num.lower() in c_text]
                if found_prev:
                    mentioned_invs = found_prev
                    break

        # 2. Approval Step: Propose action (DATEV booking or Mark Paid)
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

        # 7. Task 4: Exclude-last-N & Last-N recency + Aggregate / Totals / Counts / Due Dates
        m_excl = re.search(
            r"(?:exclud(?:e|ing)\s+(?:the\s+)?last|without\s+(?:the\s+)?last|ohne\s+die\s+letzten|außer\s+den\s+letzten|ausser\s+den\s+letzten)\s+(\d+)",
            q_low,
        )
        if m_excl:
            excl_n = int(m_excl.group(1))
            flt: Dict[str, Any] = {"sort": "uploaded_at_desc", "exclude_last": excl_n}
            if "unpaid" in q_low or "offen" in q_low:
                flt["payment_status"] = "unpaid"
            elif "paid" in q_low or "bezahlt" in q_low:
                flt["payment_status"] = "paid"
            return {"route": "aggregate", "filter": flt}

        m_last_n = re.search(r"\b(?:last|letzten|letzte)\s+(\d+)\b", q_low)
        if m_last_n or any(w in q_low for w in [
            "last uploaded", "most recent", "zuletzt hochgeladen", "neueste", "letzte rechnung"
        ]):
            n_val = int(m_last_n.group(1)) if m_last_n else 5
            flt = {"sort": "uploaded_at_desc", "limit": n_val}
            if "unpaid" in q_low or "offen" in q_low:
                flt["payment_status"] = "unpaid"
            elif "paid" in q_low or "bezahlt" in q_low:
                flt["payment_status"] = "paid"
            return {"route": "aggregate", "filter": flt}

        if any(w in q_low for w in [
            "how many", "wie viele", "count", "anzahl", "total", "summe", "gesamt",
            "spend", "umsatz", "ausgaben", "average", "durchschnitt", "top", "rank",
            "unpaid", "offen", "paid", "bezahlt", "skonto", "cashflow", "fällig", "due",
            "days remaining", "verbleibende tage"
        ]) and not mentioned_invs:
            flt = {}
            if "unpaid" in q_low or "offen" in q_low:
                flt["payment_status"] = "unpaid"
            elif "paid" in q_low or "bezahlt" in q_low:
                flt["payment_status"] = "paid"
            return {"route": "aggregate", "filter": flt}

        # 8. Specific invoice or full-text search
        return {"route": "sql_search", "query": q, "target_invoices": mentioned_invs}

    def _format_validation_summary(self, target_invoices: List[str]) -> str:
        is_en = (self.lang == "en")
        all_invs = get_all_invoices(user_id=self.user_id)
        if not all_invs:
            return (
                "No invoices are currently available for validation."
                if is_en
                else "Es sind aktuell keine Rechnungen zur Prüfung vorhanden."
            )

        invs = [
            inv for inv in all_invs
            if not target_invoices or inv.get("invoice_number") in target_invoices
        ]
        if not invs:
            invs = all_invs

        header = (
            "### Validation Report Summary (9-Rule Check)\n"
            if is_en
            else "### Prüfbericht-Zusammenfassung (9-Stufen-Validierung)\n"
        )
        lines = [header]
        for inv in invs:
            inv_num = inv.get("invoice_number", "N/A")
            cite = _cite(inv_num)
            ccy = str(inv.get("currency") or "EUR").upper()
            vr = inv.get("validation_report") or {}
            overall = vr.get("overall_status") or inv.get("validation_status") or "PENDING"
            xrechnung_ready = vr.get("xrechnung_ready", False)
            checks = vr.get("checks") or []
            failed_checks = [c for c in checks if c.get("status") == "FAIL"]
            unverified_checks = [c for c in checks if c.get("status") == "CANNOT VERIFY"]

            if is_en:
                lines.append(
                    f"- **{cite}** ({inv.get('vendor_name')}, Gross: {_fmt_money_ccy(inv.get('total_amount'), ccy)}): "
                    f"Status **{overall}** | XRechnung-ready: **{'YES' if xrechnung_ready else 'NO'}**"
                )
            else:
                lines.append(
                    f"- **{cite}** ({inv.get('vendor_name')}, Brutto: {_fmt_money_ccy(inv.get('total_amount'), ccy)}): "
                    f"Status **{overall}** | XRechnung-fähig: **{'JA' if xrechnung_ready else 'NEIN'}**"
                )
            for fc in failed_checks:
                chk_name = fc.get("name_en") if (is_en and fc.get("name_en")) else fc.get("name")
                lines.append(f"  - **[FAIL] {fc.get('code')} ({chk_name})**: {fc.get('message')}")
                for d in fc.get("discrepancies") or []:
                    if is_en:
                        lines.append(
                            f"    - Line {d.get('line', '-')}: Expected `{format_number_de_val(d.get('expected'), ccy)}`, "
                            f"Printed `{format_number_de_val(d.get('printed'), ccy)}`, "
                            f"Difference `{format_number_de_val(d.get('difference'), ccy)}`"
                        )
                    else:
                        lines.append(
                            f"    - Position {d.get('line', '-')}: Erwartet `{format_number_de_val(d.get('expected'), ccy)}`, "
                            f"Gedruckt `{format_number_de_val(d.get('printed'), ccy)}`, "
                            f"Differenz `{format_number_de_val(d.get('difference'), ccy)}`"
                        )
            for uc in unverified_checks:
                chk_name = uc.get("name_en") if (is_en and uc.get("name_en")) else uc.get("name")
                lines.append(f"  - **[CANNOT VERIFY] {uc.get('code')} ({chk_name})**: {uc.get('message')}")
            if not failed_checks and not unverified_checks:
                lines.append(
                    "  - All 9 validation checks (V1 to V9) passed (PASS)."
                    if is_en
                    else "  - Alle 9 Prüfungen (V1 bis V9) erfolgreich bestanden (PASS)."
                )

        return "\n".join(lines)

    def _phrase_aggregate_answer(self, user_query: str, agg_data: Dict[str, Any], context: Dict[str, Any]) -> str:
        """
        Phrase the answer from exact `compute_invoice_aggregate` output in `self.lang`.
        Never computes numbers in the LLM; never leaks internal tool/database names.
        """
        is_en = (self.lang == "en")

        if agg_data.get("mode") == "line_item":
            q_item = agg_data.get("line_item_query") or ("Item" if is_en else "Artikel")
            vendors = agg_data.get("vendors") or {}
            if not vendors:
                return (
                    f"No matching invoice line items were found for `{q_item}`."
                    if is_en
                    else f"Für den Suchbegriff `{q_item}` wurden keine passenden Rechnungspositionen gefunden."
                )
            lines = [
                f"### Price Comparison for `{q_item}`\n"
                if is_en
                else f"### Preisvergleich für `{q_item}`\n"
            ]
            for v_name, v_info in vendors.items():
                prices_str = ", ".join(format_eur_de(p) for p in (v_info.get("unit_prices") or []))
                eff_str = ", ".join(f"{format_eur_de(p)} / unit" if is_en else f"{format_eur_de(p)} / Einh." for p in (v_info.get("effective_unit_prices") or []))
                invs_cites = ", ".join(_cite(i) for i in (v_info.get("invoices") or []))
                if is_en:
                    cheap_badge = " **[CHEAPEST]**" if v_info.get("is_cheapest") else ""
                    lines.append(
                        f"- **{v_name}**{cheap_badge}: Unit price {prices_str} "
                        f"(Effective price per unit: {eff_str}) | Matching lines: {v_info.get('matching_lines')} | "
                        f"Invoices: {invs_cites}"
                    )
                else:
                    cheap_badge = " **[GÜNSTIGSTER ANBIETER]**" if v_info.get("is_cheapest") else ""
                    lines.append(
                        f"- **{v_name}**{cheap_badge}: Einzelpreis {prices_str} "
                        f"(Effektivpreis je Einheit: {eff_str}) | Positionen: {v_info.get('matching_lines')} | "
                        f"Belege: {invs_cites}"
                    )
            return "\n".join(lines)

        count = agg_data.get("count", 0)
        excl_n = int(agg_data.get("exclude_last") or 0)
        req_limit = int(agg_data.get("requested_limit") or 0)

        if count == 0:
            if excl_n > 0:
                return (
                    f"Excluding the last {excl_n} leaves no invoices (Invoice count: 0, Total: 0,00 €)."
                    if is_en
                    else f"Ohne die letzten {excl_n} bleiben keine Rechnungen übrig (Anzahl: 0, Summe: 0,00 €)."
                )
            return (
                "No invoices matched the requested criteria (Invoice count: 0, Total: 0,00 €)."
                if is_en
                else "Es wurden keine Rechnungen gefunden, die den Filterkriterien entsprechen (Anzahl: 0, Summe: 0,00 €)."
            )

        agg_ccy = str(agg_data.get("currency") or "EUR")
        by_currency = agg_data.get("by_currency") or {}
        unconverted_foreign = agg_data.get("unconverted_foreign") or []

        lines = [
            "### Summary\n" if is_en else "### Auswertung\n",
        ]

        if req_limit > 0 and count < req_limit:
            lines.append(
                f"Only {count} {'invoice exists' if count == 1 else 'invoices exist'}."
                if is_en
                else f"Es {'existiert' if count == 1 else 'existieren'} nur {count} {'Rechnung' if count == 1 else 'Rechnungen'}."
            )

        if agg_ccy == "MIXED":
            # Task 6: Never sum across currencies into one EUR total
            subtotals_parts = [
                _fmt_money_ccy(cb["total_amount"], ccy)
                for ccy, cb in by_currency.items()
            ]
            combined_display = " + ".join(subtotals_parts)
            unconv_cites = ", ".join(_cite(i) for i in unconverted_foreign)
            if is_en:
                lines.append(f"- **Invoice count**: {count}")
                lines.append(f"- **Per-currency totals**: **{combined_display}**")
                lines.append("- **Note**: Multiple currencies are present, so no single combined EUR total applies.")
                for ccy, cb in by_currency.items():
                    lines.append(
                        f"  - **{ccy}** ({cb['count']} {'invoice' if cb['count'] == 1 else 'invoices'}): "
                        f"Net {_fmt_money_ccy(cb['subtotal'], ccy)}, VAT {_fmt_money_ccy(cb['tax_amount'], ccy)}, "
                        f"Gross **{_fmt_money_ccy(cb['total_amount'], ccy)}**"
                    )
                if unconverted_foreign:
                    lines.append(f"- **Unconverted foreign invoices**: {unconv_cites}")
            else:
                lines.append(f"- **Anzahl Belege**: {count}")
                lines.append(f"- **Summen je Währung**: **{combined_display}**")
                lines.append("- **Hinweis**: Es liegen mehrere Währungen vor; eine einheitliche Gesamtsumme in EUR ist nicht zulässig.")
                for ccy, cb in by_currency.items():
                    lines.append(
                        f"  - **{ccy}** ({cb['count']} Beleg(e)): "
                        f"Netto {_fmt_money_ccy(cb['subtotal'], ccy)}, USt {_fmt_money_ccy(cb['tax_amount'], ccy)}, "
                        f"Brutto **{_fmt_money_ccy(cb['total_amount'], ccy)}**"
                    )
                if unconverted_foreign:
                    lines.append(f"- **Nicht umgerechnete Fremdwährungsrechnungen**: {unconv_cites}")
        else:
            tot_str = _fmt_money_ccy(agg_data.get("total_amount", 0.0), agg_ccy)
            sub_str = _fmt_money_ccy(agg_data.get("subtotal", 0.0), agg_ccy)
            tax_str = _fmt_money_ccy(agg_data.get("tax_amount", 0.0), agg_ccy)
            avg_str = _fmt_money_ccy(agg_data.get("average_total", 0.0), agg_ccy)
            skonto_str = _fmt_money_ccy(agg_data.get("skonto_potential", 0.0), agg_ccy)

            if is_en:
                lines.extend([
                    f"- **Invoice count**: {count}",
                    f"- **Total (gross)**: **{tot_str}**",
                    f"- **Net total**: {sub_str}",
                    f"- **VAT total**: {tax_str}",
                    f"- **Average invoice value**: {avg_str}",
                ])
                if agg_data.get("skonto_potential", 0.0) > 0:
                    lines.append(f"- **Available Skonto savings**: {skonto_str}")
            else:
                lines.extend([
                    f"- **Anzahl Belege**: {count}",
                    f"- **Gesamtbetrag (Brutto)**: **{tot_str}**",
                    f"- **Nettobetrag**: {sub_str}",
                    f"- **Umsatzsteuer (USt gesamt)**: {tax_str}",
                    f"- **Durchschnittlicher Belegwert**: {avg_str}",
                ])
                if agg_data.get("skonto_potential", 0.0) > 0:
                    lines.append(f"- **Verfügbares Skonto-Potenzial**: {skonto_str}")

        if agg_data.get("disambiguation"):
            lines.append(f"\n{'Note' if is_en else 'Hinweis'}: {agg_data['disambiguation']}")

        by_vendor = agg_data.get("by_vendor") or {}
        if by_vendor:
            lines.append("\n**Breakdown by vendor:**" if is_en else "\n**Aufschlüsselung nach Lieferant:**")
            for v_name, v_stat in sorted(by_vendor.items(), key=lambda x: x[1]["total_amount"], reverse=True):
                cites = ", ".join(_cite(i) for i in v_stat.get("invoices", []))
                v_ccy = v_stat.get("currency", "EUR")
                if v_ccy == "MIXED":
                    lines.append(
                        f"- **{v_name}**: {v_stat['count']} {'invoice(s)' if is_en else 'Beleg(e)'} "
                        f"| {'Sources' if is_en else 'Quellen'}: {cites}"
                    )
                elif is_en:
                    lines.append(
                        f"- **{v_name}**: {v_stat['count']} invoice(s), Gross **{_fmt_money_ccy(v_stat['total_amount'], v_ccy)}** "
                        f"(Net {_fmt_money_ccy(v_stat['subtotal'], v_ccy)}, VAT {_fmt_money_ccy(v_stat['tax_amount'], v_ccy)}) "
                        f"| Sources: {cites}"
                    )
                else:
                    lines.append(
                        f"- **{v_name}**: {v_stat['count']} Beleg(e), Brutto **{_fmt_money_ccy(v_stat['total_amount'], v_ccy)}** "
                        f"(Netto {_fmt_money_ccy(v_stat['subtotal'], v_ccy)}, USt {_fmt_money_ccy(v_stat['tax_amount'], v_ccy)}) "
                        f"| Quellen: {cites}"
                    )

        invs = agg_data.get("invoices") or []
        if invs:
            lines.append("\n**Individual invoices (sources):**" if is_en else "\n**Einzelbelege (Quellen):**")
            for r in invs[:10]:
                r_ccy = r.get("currency", "EUR")
                dt_de = format_date_de(r.get("invoice_date")) or r.get("invoice_date", "N/A")
                due_raw = r.get("due_date")
                due_de = format_date_de(due_raw) if due_raw else ("none" if is_en else "keine")
                days_text = _format_days_remaining(r.get("days_until_due"), lang=self.lang)
                if is_en:
                    lines.append(
                        f"- **{_cite(r['invoice_number'])}** | {r['vendor_name']} | Date: {dt_de} | "
                        f"Due: {due_de} ({days_text}) | Net: {_fmt_money_ccy(r['subtotal'], r_ccy)} | "
                        f"VAT: {_fmt_money_ccy(r['tax_amount'], r_ccy)} | Gross: **{_fmt_money_ccy(r['total_amount'], r_ccy)}** | "
                        f"Status: {r['payment_status']}"
                    )
                else:
                    lines.append(
                        f"- **{_cite(r['invoice_number'])}** | {r['vendor_name']} | Datum: {dt_de} | "
                        f"Fällig: {due_de} ({days_text}) | Netto: {_fmt_money_ccy(r['subtotal'], r_ccy)} | "
                        f"USt: {_fmt_money_ccy(r['tax_amount'], r_ccy)} | Brutto: **{_fmt_money_ccy(r['total_amount'], r_ccy)}** | "
                        f"Status: {r['payment_status']}"
                    )

        if GROQ_API_KEY:
            try:
                from langchain_groq import ChatGroq
                from langchain_core.prompts import ChatPromptTemplate

                persona = (
                    "You are an English accounting copilot. Answer in English."
                    if is_en
                    else "You are a German accounting copilot. Answer in German."
                )

                llm = ChatGroq(model=GROQ_MODEL_NAME, api_key=GROQ_API_KEY, temperature=0, max_tokens=600)
                prompt = ChatPromptTemplate.from_template(
                    """{persona}
Write a short, direct 2-sentence answer to the user's question using ONLY the exact pre-computed figures below.
Never compute any math yourself, never change any numbers, and never use em dashes.
Always include clickable invoice links like [RE-2026-10418](#invoice/RE-2026-10418).

User Question: "{query}"
Pre-computed Tool Result:
{tool_text}

Concise Lead-in:"""
                )
                resp = (prompt | llm).invoke({
                    "persona": persona,
                    "query": user_query,
                    "tool_text": "\n".join(lines),
                })
                lead = getattr(resp, "content", "").strip().replace("\u2014", "-")
                if lead:
                    return f"{lead}\n\n" + "\n".join(lines)
            except Exception:
                pass

        return "\n".join(lines)

    def run(self, user_query: str, idempotency_key: Optional[str] = None, lang: Optional[str] = None) -> str:
        """
        Process a user message through the thin router and return the assistant response.
        """
        if lang is not None:
            self.lang = "en" if str(lang).strip().lower().startswith("en") else "de"
        is_en = (self.lang == "en")

        set_current_user_id(self.user_id)

        if idempotency_key:
            existing_msgs = get_messages(self.conversation_id, self.user_id, db_path=self.db_path)
            for idx, m in enumerate(existing_msgs):
                if m.get("idempotency_key") == idempotency_key:
                    for follow in existing_msgs[idx + 1:]:
                        if follow.get("role") == "assistant":
                            return follow.get("content", "")

        save_message(
            conversation_id=self.conversation_id,
            role="user",
            content=user_query,
            idempotency_key=idempotency_key,
            db_path=self.db_path,
        )

        try:
            maybe_summarise(
                conversation_id=self.conversation_id,
                summarise_threshold=self.summarise_threshold,
                recent_window=self.recent_window,
                db_path=self.db_path,
            )
        except Exception as exc:
            logger.warning("Summarisation step failed (non-fatal): %s", exc)

        context = build_context_block(
            conversation_id=self.conversation_id,
            recent_window=self.recent_window,
            db_path=self.db_path,
        )

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
                ccy = str(ext.get("currency") or "EUR").upper()
                cite = _cite(inv_no) if store else inv_no
                if is_en:
                    final_response = (
                        f"Invoice **{cite}** (`{res.get('file_name')}`) has been extracted"
                        + (" and saved.\n\n" if store else " (preview only, not saved).\n\n")
                        + f"- **Vendor**: {ext.get('vendor_name', 'N/A')} (VAT ID: {ext.get('vat_id') or 'missing'})\n"
                        + f"- **Invoice date**: {format_date_de(ext.get('invoice_date')) or ext.get('invoice_date', 'N/A')}\n"
                        + f"- **Due date**: {format_date_de(ext.get('due_date')) or ext.get('due_date', 'N/A')}\n"
                        + f"- **Net total**: {_fmt_money_ccy(ext.get('subtotal', 0.0), ccy)}\n"
                        + f"- **VAT total**: {_fmt_money_ccy(ext.get('tax_amount', 0.0), ccy)}\n"
                        + f"- **Total (gross)**: **{_fmt_money_ccy(ext.get('total_amount', 0.0), ccy)}**\n"
                        + f"- **Validation status**: {ext.get('validation_status', 'PENDING')}"
                    )
                else:
                    final_response = (
                        f"Beleg **{cite}** (`{res.get('file_name')}`) wurde extrahiert"
                        + (" und gespeichert.\n\n" if store else " (nur Vorschau, nicht gespeichert).\n\n")
                        + f"- **Lieferant**: {ext.get('vendor_name', 'N/A')} (USt-IdNr.: {ext.get('vat_id') or 'fehlt'})\n"
                        + f"- **Rechnungsdatum**: {format_date_de(ext.get('invoice_date')) or ext.get('invoice_date', 'N/A')}\n"
                        + f"- **Fälligkeitsdatum**: {format_date_de(ext.get('due_date')) or ext.get('due_date', 'N/A')}\n"
                        + f"- **Nettobetrag**: {_fmt_money_ccy(ext.get('subtotal', 0.0), ccy)}\n"
                        + f"- **Umsatzsteuer**: {_fmt_money_ccy(ext.get('tax_amount', 0.0), ccy)}\n"
                        + f"- **Bruttobetrag**: **{_fmt_money_ccy(ext.get('total_amount', 0.0), ccy)}**\n"
                        + f"- **Prüfstatus**: {ext.get('validation_status', 'GEPRÜFT')}"
                    )

            elif route == "propose_action":
                act_type = intent["action_type"]
                targets = intent.get("target_invoices") or []
                if not targets:
                    final_response = (
                        "No matching invoices were found for this action."
                        if is_en
                        else "Es wurden keine passenden Belege für diese Aktion gefunden."
                    )
                else:
                    if is_en:
                        label = (
                            "DATEV booking transfer (SKR03/SKR04)"
                            if act_type == "post_datev"
                            else "Mark status as PAID"
                        )
                    else:
                        label = (
                            "Buchungsübergabe an DATEV (SKR03/SKR04)"
                            if act_type == "post_datev"
                            else "Statusänderung auf BEZAHLT (Paid)"
                        )
                    cites = ", ".join(_cite(t) for t in targets)
                    desc = f"{label}: {', '.join(targets)}"
                    proposal = create_copilot_action_proposal(
                        user_id=self.user_id,
                        conversation_id=self.conversation_id,
                        action_type=act_type,
                        target_invoices=targets,
                        description=desc,
                        db_path=self.db_path,
                    )
                    if is_en:
                        final_response = (
                            f"### Approval required: {label}\n\n"
                            f"I have prepared the following action for {cites}:\n"
                            f"- **Action**: {label}\n"
                            f"- **Target invoices**: {cites}\n\n"
                            f"Please confirm execution using the approval button below. "
                            f"No changes will be made without human approval.\n\n"
                            f"[ACTION_PROPOSAL: {proposal['action_id']}]"
                        )
                    else:
                        final_response = (
                            f"### Freigabe erforderlich: {label}\n\n"
                            f"Ich habe folgende Aktion für {cites} vorbereitet:\n"
                            f"- **Aktion**: {label}\n"
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
                    final_response = (
                        f"Excel export could not be created: {res.get('message')}"
                        if is_en
                        else f"Excel-Export konnte nicht erstellt werden: {res.get('message')}"
                    )

            elif route == "graph":
                res = self._graph_agent.run(user_query)
                chart_paths = res.get("chart_paths") or []
                tags = "".join(f"\n[CHART: {cp}]" for cp in chart_paths)
                default_msg = "Visualization created." if is_en else "Visualisierung erstellt."
                final_response = f"{res.get('message', default_msg)}{tags}"

            elif route == "validation":
                final_response = self._format_validation_summary(intent.get("target_invoices") or [])

            elif route == "aggregate":
                agg_data = compute_invoice_aggregate(intent.get("filter") or {}, user_id=self.user_id)
                final_response = self._phrase_aggregate_answer(user_query, agg_data, context)

            else:
                qa_res = self._qa_agent.answer(user_query, chat_history=context.get("lc_messages"), lang=self.lang)
                final_response = qa_res.get("answer", "")

        except Exception as exc:
            logger.error("Orchestrator routing error: %s", exc, exc_info=True)
            final_response = (
                f"An error occurred while processing your request: {str(exc)}"
                if is_en
                else f"Bei der Verarbeitung der Anfrage ist ein Fehler aufgetreten: {str(exc)}"
            )

        final_response = final_response.replace("\u2014", "-")

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


def format_number_de_val(val: Any, currency: str = "EUR") -> str:
    if val is None or val == "":
        return "-"
    if isinstance(val, (int, float)):
        return _fmt_money_ccy(val, currency)
    try:
        return _fmt_money_ccy(float(val), currency)
    except Exception:
        return str(val)