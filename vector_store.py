"""
vector_store.py - SQL-Only Invoice Storage & FTS5 Search Engine (Task 1).

ChromaDB vector search has been completely replaced with SQLite + FTS5.
All invoice persistence, retrieval, aggregation, and full-text search run
directly against the SQLite `invoices` table and `invoices_fts` FTS5 index.
"""

import os
import re
import json
import uuid
import sqlite3
import logging
import contextvars as _contextvars
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import pandas as pd

from config import CHAT_DB_PATH, DEFAULT_CHROMA_PATH, DEFAULT_INDEX_PATH
from chat_store import init_db

logger = logging.getLogger(__name__)

_CURRENT_USER_ID: "_contextvars.ContextVar[Optional[str]]" = _contextvars.ContextVar(
    "autoinvoice_current_user_id", default=None
)


def set_current_user_id(user_id: Optional[str]) -> None:
    """Set the user scope for invoice-data reads in this context."""
    _CURRENT_USER_ID.set(str(user_id) if user_id is not None else None)


def _resolve_user_id(user_id: Optional[str]) -> Optional[str]:
    if user_id is not None:
        return str(user_id)
    return _CURRENT_USER_ID.get()


def _user_id_variants(user_id: Optional[str]) -> List[str]:
    """Return both hyphenated and hex UUID variants for user_id matching."""
    uid = _resolve_user_id(user_id)
    if not uid:
        return []
    u_str = str(uid).strip()
    u_clean = u_str.replace("-", "")
    variants = [u_str]
    if u_clean != u_str:
        variants.append(u_clean)
    try:
        u_obj = uuid.UUID(u_str)
        for v in (str(u_obj), u_obj.hex):
            if v not in variants:
                variants.append(v)
    except Exception:
        pass
    return variants


def load_index(index_path: str = DEFAULT_CHROMA_PATH) -> str:
    """Compatibility helper returning the active SQLite database path."""
    init_db(CHAT_DB_PATH)
    return CHAT_DB_PATH


GERMAN_MONTHS = {
    "januar": 1, "jan": 1,
    "februar": 2, "feb": 2,
    "märz": 3, "maerz": 3, "mrz": 3,
    "april": 4, "apr": 4,
    "mai": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "dezember": 12, "dez": 12,
}



def parse_german_decimal_strict(val: Any, field_name: str = "amount") -> float:
    """
    Strictly parse a German or standard decimal value (e.g. "1.234,56" -> 1234.56,
    "1.500" -> 1500.0, "225,80-" -> -225.80). Raises ValueError naming `field_name`
    if `val` is empty or unparseable (e.g. "abc"), never silently falling back to 0.
    """
    if val is None or isinstance(val, bool):
        raise ValueError(f"Ungueltiger Zahlenwert im Feld '{field_name}': Wert fehlt.")
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        raise ValueError(f"Ungueltiger Zahlenwert im Feld '{field_name}': Leerer Wert.")
    s = re.sub(r"(?:EUR|USD|INR|GBP|CHF|€|\$|₹|£|%)", "", s, flags=re.IGNORECASE).strip()
    if not s:
        raise ValueError(f"Ungueltiger Zahlenwert im Feld '{field_name}': '{val}'.")

    negative = False
    if s.endswith("-"):
        negative = True
        s = s[:-1].strip()
    elif s.startswith("-"):
        negative = True
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()

    if not s:
        raise ValueError(f"Ungueltiger Zahlenwert im Feld '{field_name}': '{val}'.")

    if re.match(r"^(?:\d{1,3}(?:\.\d{3})+|\d+),\d+$", s):
        norm_s = s.replace(".", "").replace(",", ".")
    elif re.match(r"^\d{1,3}(?:\.\d{3})+$", s):
        norm_s = s.replace(".", "")
    elif re.match(r"^\d{1,3}(?:,\d{3})+\.\d+$", s):
        norm_s = s.replace(",", "")
    elif re.match(r"^\d+(?:\.\d+)?$", s):
        norm_s = s
    else:
        raise ValueError(
            f"Ungueltiger Zahlenwert im Feld '{field_name}': '{val}' kann nicht als Dezimalzahl gelesen werden."
        )

    res = float(norm_s)
    return -res if negative else res


def parse_numeric(val: Any, default: float = 0.0) -> float:
    """
    Parse numeric currency or float values, supporting both German format
    (1.234,56, 1.500, 225,80-, 0,152 €) and standard float strings (1234.56).
    """
    if val is None or isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if not val_str or val_str.lower() in ("n/a", "none", "null", "unknown", "-", ""):
        return default

    trailing_minus = False
    cleaned_tokens = re.sub(r"(?:EUR|USD|INR|GBP|CHF|€|\$|₹|£|%|Stk|psch|m)\b", "", val_str, flags=re.IGNORECASE).strip()
    cleaned_tokens = cleaned_tokens.replace("€", "").replace("$", "").replace("₹", "").strip()
    if cleaned_tokens.endswith("-"):
        trailing_minus = True
        cleaned_tokens = cleaned_tokens[:-1].strip()
    elif cleaned_tokens.startswith("-"):
        trailing_minus = True
        cleaned_tokens = cleaned_tokens[1:].strip()

    if "." in cleaned_tokens and "," in cleaned_tokens:
        if cleaned_tokens.rfind(",") > cleaned_tokens.rfind("."):
            # German: 1.234,56 -> 1234.56
            cleaned_tokens = cleaned_tokens.replace(".", "").replace(",", ".")
        else:
            # English: 1,234.56 -> 1234.56
            cleaned_tokens = cleaned_tokens.replace(",", "")
    elif "," in cleaned_tokens:
        parts = cleaned_tokens.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2, 3):
            cleaned_tokens = cleaned_tokens.replace(",", ".")
        else:
            cleaned_tokens = cleaned_tokens.replace(",", "")
    elif "." in cleaned_tokens:
        # German integer thousands grouping: e.g. "1.000", "1.500", "25.000"
        if re.match(r"^\d{1,3}(?:\.\d{3})+$", cleaned_tokens):
            cleaned_tokens = cleaned_tokens.replace(".", "")

    cleaned_num = re.sub(r"[^\d.]", "", cleaned_tokens)
    if not cleaned_num:
        return default
    try:
        res = float(cleaned_num)
        return -res if trailing_minus else res
    except (ValueError, TypeError):
        return default


def parse_german_date(val: Any) -> str:
    """
    Parse a German or ISO date string into canonical YYYY-MM-DD format.
    """
    if not val:
        return ""
    s = str(val).strip()
    if not s or s.lower() in ("n/a", "none", "null", "unknown", "-"):
        return ""

    m_iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)
    if m_iso:
        y, mo, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"

    m_dmy = re.search(r"\b(\d{1,2})[./\s]+(\d{1,2})[./\s]+(\d{4})\b", s)
    if m_dmy:
        d, mo, y = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"

    m_named = re.search(r"\b(\d{1,2})\.?\s+([A-Za-zÄÖÜäöüß]+)\s+(\d{4})\b", s)
    if m_named:
        d = int(m_named.group(1))
        m_name = m_named.group(2).lower()
        y = int(m_named.group(3))
        mo = GERMAN_MONTHS.get(m_name)
        if mo and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"

    return s


def format_number_de(val: Any, decimals: int = 2) -> str:
    """Format a number in German locale: 1234.56 -> '1.234,56'."""
    num = parse_numeric(val, 0.0)
    sign = "-" if num < 0 else ""
    abs_num = abs(num)
    formatted = f"{abs_num:,.{decimals}f}"
    de_str = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}{de_str}"


def format_eur_de(val: Any) -> str:
    """Format a monetary amount in German EUR format: '1.234,56 €'."""
    return f"{format_number_de(val, 2)} €"


def format_date_de(val: Any) -> str:
    """Format a date string into German DD.MM.YYYY format."""
    iso = parse_german_date(val)
    if not iso:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", iso)
    if m:
        return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
    return str(val)


def normalize_invoice_data(raw_data: dict, file_name: str = "") -> dict:
    """
    Multi-schema normalizer for German & international invoice dicts.
    Strictly preserves printed numbers for validation and never fabricates subtotal
    from total_amount (derives subtotal only from line items if missing, else 0.0).
    """
    if not isinstance(raw_data, dict):
        raw_data = {}

    fields = raw_data.get("fields") if isinstance(raw_data.get("fields"), dict) else {}

    def extract_clean_str(v):
        if v is None:
            return ""
        if isinstance(v, dict):
            for sub_key in ("name", "vendor_name", "company_name", "seller", "title", "vendor"):
                if v.get(sub_key) and str(v[sub_key]).strip():
                    return str(v[sub_key]).strip()
            return ""
        s = str(v).strip()
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("(") and s.endswith(")")):
            try:
                parsed = json.loads(s.replace("'", '"'))
                if isinstance(parsed, dict):
                    return extract_clean_str(parsed)
            except Exception:
                m = re.search(r"['\"](?:name|vendor_name|company|seller)['\"]\s*:\s*['\"]([^'\"]+)['\"]", s, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
        return s

    def find_str(*keys, default=""):
        for k in keys:
            if k in raw_data and raw_data.get(k) is not None:
                val = extract_clean_str(raw_data.get(k))
                if val and val.lower() not in ("none", "null", "n/a", "unknown"):
                    return val
            if k in fields and fields.get(k) is not None:
                val = extract_clean_str(fields.get(k))
                if val and val.lower() not in ("none", "null", "n/a", "unknown"):
                    return val
        return default

    def find_float(*keys, default=0.0):
        for k in keys:
            if k in raw_data and raw_data[k] is not None and str(raw_data[k]).strip() != "":
                amt = parse_numeric(raw_data[k], default=default)
                if amt != 0.0:
                    return amt
            if k in fields and fields[k] is not None and str(fields[k]).strip() != "":
                amt = parse_numeric(fields[k], default=default)
                if amt != 0.0:
                    return amt
        return default

    inv_num = find_str(
        "invoice_number", "rechnungsnummer", "invoice_no", "inv_number", "inv_no",
        "invoice_id", "bill_number", "gutschrift_nr", default=""
    )
    if not inv_num:
        if file_name:
            inv_num = os.path.splitext(os.path.basename(file_name))[0]
        else:
            inv_num = "N/A"

    vendor = find_str(
        "vendor_name", "vendor", "seller", "seller_name", "lieferant",
        "company", "company_name", "merchant", "issuer", default="Unknown Vendor"
    )
    vendor_addr = find_str("vendor_address", "seller_address", "company_address", "lieferantenanschrift", default="")
    vat_id = find_str("vat_id", "vendor_vat_id", "ust_idnr", "ust_id", "vat_number", default="")
    if vat_id:
        vat_id = re.sub(r"\s+", "", vat_id).upper()
    tax_number = find_str("tax_number", "vendor_tax_number", "steuernummer", "steuer_nr", default="")
    vendor_iban = find_str("vendor_iban", "iban", "bank_iban", default="")
    vendor_bic = find_str("vendor_bic", "bic", "swift", default="")

    buyer = find_str(
        "buyer_name", "buyer", "customer", "customer_name", "rechnungsempfaenger",
        "client", "client_name", "bill_to", "recipient", default="N/A"
    )
    buyer_addr = find_str("buyer_address", "customer_address", "client_address", "shipping_address", default="")
    buyer_vat_id = find_str("buyer_vat_id", "customer_vat_id", default="")
    leitweg_id = find_str("leitweg_id", "buyer_reference", "bt_10", default="")

    doc_type = find_str("document_type", "type", "belegart", default="invoice").lower()
    if "gutschrift" in doc_type or "credit" in doc_type or str(inv_num).upper().startswith("GS-"):
        doc_type = "credit_note"
    else:
        doc_type = "invoice"

    raw_inv_date = find_str("invoice_date", "date", "issue_date", "rechnungsdatum", "datum", default="")
    inv_date = parse_german_date(raw_inv_date) or (raw_inv_date if raw_inv_date else "N/A")

    raw_deliv_date = find_str("delivery_date", "leistungsdatum", "lieferdatum", "service_date", default="")
    delivery_date = parse_german_date(raw_deliv_date) or raw_deliv_date

    raw_due_date = find_str("due_date", "payment_due", "payment_due_date", "faelligkeitsdatum", "zahlbar_bis", default="")
    due_date = parse_german_date(raw_due_date) or raw_due_date

    payment_terms = find_str("payment_terms", "zahlungsbedingungen", "zahlungsziel", default="")
    skonto_terms = find_str("skonto_terms", "skonto", default="")
    skonto_percent = find_float("skonto_percent", default=0.0)
    skonto_days = int(parse_numeric(raw_data.get("skonto_days") or fields.get("skonto_days"), 0.0))
    raw_skonto_dl = find_str("skonto_deadline", default="")
    skonto_deadline = parse_german_date(raw_skonto_dl) or raw_skonto_dl
    skonto_amount = find_float("skonto_amount", default=0.0)

    raw_items = (
        raw_data.get("line_items")
        if raw_data.get("line_items") is not None
        else (raw_data.get("items") if raw_data.get("items") is not None else (fields.get("line_items") or fields.get("items") or []))
    )
    if isinstance(raw_items, str):
        try:
            raw_items = json.loads(raw_items)
        except Exception:
            raw_items = []
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    elif not isinstance(raw_items, list):
        raw_items = []

    normalized_items = []
    for idx, it in enumerate(raw_items, start=1):
        if not isinstance(it, dict):
            continue
        pos = str(it.get("pos") or it.get("position") or idx)
        art_no = str(it.get("article_number") or it.get("art_nr") or it.get("sku") or it.get("code") or "").strip()
        desc = str(
            it.get("description") or it.get("name") or it.get("bezeichnung")
            or it.get("desc") or it.get("item") or it.get("item_name") or it.get("product") or ""
        ).strip()
        qty = parse_numeric(it.get("quantity") if it.get("quantity") is not None else it.get("qty"), 0.0)
        unit = str(it.get("unit") or it.get("me") or it.get("uom") or "Stk").strip()
        unit_price = parse_numeric(
            it.get("unit_price") if it.get("unit_price") is not None
            else (it.get("rate") if it.get("rate") is not None else it.get("price")),
            0.0,
        )
        price_unit = parse_numeric(it.get("price_unit") if it.get("price_unit") is not None else it.get("pe"), 1.0)
        if price_unit <= 0:
            price_unit = 1.0
        disc_pct = parse_numeric(
            it.get("discount_percent") if it.get("discount_percent") is not None
            else (it.get("discount") if it.get("discount") is not None else it.get("rabatt")),
            0.0,
        )
        line_total = parse_numeric(
            it.get("line_total") if it.get("line_total") is not None
            else (it.get("total") if it.get("total") is not None else it.get("amount")),
            0.0,
        )
        vat_rate = parse_numeric(
            it.get("vat_rate") if it.get("vat_rate") is not None
            else (it.get("tax_rate") if it.get("tax_rate") is not None else it.get("ust")),
            19.0,
        )
        normalized_items.append({
            "pos": pos,
            "article_number": art_no,
            "description": desc,
            "name": desc,
            "quantity": qty,
            "qty": qty,
            "unit": unit,
            "unit_price": unit_price,
            "rate": unit_price,
            "price_unit": price_unit,
            "discount_percent": disc_pct,
            "discount": disc_pct,
            "line_total": line_total,
            "total": line_total,
            "vat_rate": vat_rate,
        })

    raw_vat_bd = raw_data.get("vat_breakdown") or fields.get("vat_breakdown") or []
    if isinstance(raw_vat_bd, str):
        try:
            raw_vat_bd = json.loads(raw_vat_bd)
        except Exception:
            raw_vat_bd = []
    vat_breakdown = []
    if isinstance(raw_vat_bd, list):
        for vb in raw_vat_bd:
            if isinstance(vb, dict) and vb.get("rate") is not None:
                vat_breakdown.append({
                    "rate": parse_numeric(vb.get("rate"), 19.0),
                    "net": parse_numeric(vb.get("net"), 0.0),
                    "vat": parse_numeric(vb.get("vat"), 0.0),
                })

    subtotal = find_float("subtotal", "sub_total", "amount_before_tax", "net_amount", "nettobetrag", default=0.0)
    tax_amount = find_float("tax_amount", "tax", "vat", "gst", "total_tax", "ust_betrag", default=0.0)
    discount = find_float("discount", "discount_amount", "rebate", default=0.0)
    total = find_float(
        "total_amount", "grand_total", "total", "amount_due",
        "final_total", "rechnungsbetrag", "gesamtbetrag", default=0.0
    )

    # Guardrail: Never fabricate a subtotal from total_amount.
    # If subtotal is missing (0.0), derive it from line items, else store 0.0.
    if subtotal == 0.0 and normalized_items:
        derived_sub = sum(parse_numeric(it.get("line_total"), 0.0) for it in normalized_items)
        if derived_sub != 0.0:
            subtotal = round(derived_sub, 2)

    if tax_amount == 0.0 and vat_breakdown:
        derived_tax = sum(parse_numeric(vb.get("vat"), 0.0) for vb in vat_breakdown)
        if derived_tax != 0.0:
            tax_amount = round(derived_tax, 2)

    if total == 0.0 and subtotal != 0.0:
        total = round(subtotal + tax_amount, 2)

    currency = find_str("currency", "currency_code", "currency_symbol", default="EUR").upper()
    if currency in ("€", "EURO"):
        currency = "EUR"
    payment_status = find_str("payment_status", "status", default="Unpaid")

    return {
        "invoice_number": inv_num,
        "vendor_name": vendor,
        "vendor_address": vendor_addr,
        "vat_id": vat_id,
        "tax_number": tax_number,
        "vendor_iban": vendor_iban,
        "vendor_bic": vendor_bic,
        "buyer_name": buyer,
        "buyer_address": buyer_addr,
        "buyer_vat_id": buyer_vat_id,
        "leitweg_id": leitweg_id,
        "document_type": doc_type,
        "invoice_date": inv_date,
        "delivery_date": delivery_date,
        "due_date": due_date,
        "payment_terms": payment_terms,
        "skonto_terms": skonto_terms,
        "skonto_percent": skonto_percent,
        "skonto_days": skonto_days,
        "skonto_deadline": skonto_deadline,
        "skonto_amount": skonto_amount,
        "total_amount": total,
        "subtotal": subtotal,
        "tax": tax_amount,
        "tax_amount": tax_amount,
        "discount": discount,
        "currency": currency,
        "source_currency": find_str("source_currency", default=currency).upper() or currency,
        "fx_rate": find_float("fx_rate", default=1.0) or 1.0,
        "fx_rate_date": find_str("fx_rate_date", default=""),
        "fx_rate_source": find_str("fx_rate_source", default=""),
        "tax_treatment": find_str("tax_treatment", default="standard") or "standard",
        "original_net": find_float("original_net", default=subtotal if currency != "EUR" else 0.0),
        "original_vat": find_float("original_vat", default=tax_amount if currency != "EUR" else 0.0),
        "original_gross": find_float("original_gross", default=total if currency != "EUR" else 0.0),
        "supplier_country": find_str("supplier_country", "vendor_country", "country", default="").upper()[:2],
        "payment_status": payment_status,
        "line_items": normalized_items,
        "items": normalized_items,
        "vat_breakdown": vat_breakdown,
    }


def _build_item_descriptions_text(line_items: List[dict]) -> str:
    parts = []
    for it in line_items or []:
        if isinstance(it, dict):
            art = str(it.get("article_number") or "").strip()
            desc = str(it.get("description") or it.get("name") or "").strip()
            if art:
                parts.append(art)
            if desc:
                parts.append(desc)
    return " | ".join(parts)


def _sync_fts_row(
    conn: sqlite3.Connection,
    doc_id: str,
    user_id: str,
    vendor_name: str,
    invoice_number: str,
    line_items: List[dict],
) -> None:
    item_text = _build_item_descriptions_text(line_items)
    conn.execute("DELETE FROM invoices_fts WHERE doc_id = ?", (doc_id,))
    conn.execute(
        """
        INSERT INTO invoices_fts (doc_id, user_id, vendor_name, invoice_number, item_descriptions)
        VALUES (?, ?, ?, ?, ?)
        """,
        (doc_id, user_id, vendor_name or "", invoice_number or "", item_text),
    )


def _normalize_iban(iban: str) -> str:
    return re.sub(r"\s+", "", str(iban or "")).upper()


def _check_and_sync_vendor_iban(
    conn: sqlite3.Connection,
    user_id: str,
    norm: dict,
    vies_status: str = "unchecked",
    vies_checked_at: str = "",
) -> tuple[int, str]:
    vendor_name = str(norm.get("vendor_name") or "").strip()
    if not vendor_name or vendor_name.lower() in ("unknown vendor", "unknown", "n/a"):
        return 0, ""

    new_iban_raw = str(norm.get("vendor_iban") or "").strip()
    new_iban_clean = _normalize_iban(new_iban_raw)
    now = datetime.now(timezone.utc).isoformat()

    u_variants = _user_id_variants(user_id)
    if not u_variants:
        u_variants = [str(user_id)]

    placeholders = ",".join("?" for _ in u_variants)
    row = conn.execute(
        f"SELECT * FROM vendors WHERE user_id IN ({placeholders}) AND lower(vendor_name) = lower(?)",
        [*u_variants, vendor_name],
    ).fetchone()

    if not row:
        conn.execute(
            """
            INSERT INTO vendors (
                user_id, vendor_name, address, vat_id, tax_number,
                iban, bic, payment_terms, vies_status, vies_checked_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                u_variants[0],
                vendor_name,
                str(norm.get("vendor_address") or ""),
                str(norm.get("vat_id") or ""),
                str(norm.get("tax_number") or ""),
                new_iban_raw,
                str(norm.get("vendor_bic") or ""),
                str(norm.get("payment_terms") or ""),
                vies_status,
                vies_checked_at,
                now,
            ),
        )
        return 0, ""

    stored_iban_raw = str(row["iban"] or "").strip()
    stored_iban_clean = _normalize_iban(stored_iban_raw)

    iban_change_flag = 0
    previous_iban = ""

    if stored_iban_clean and new_iban_clean and stored_iban_clean != new_iban_clean:
        iban_change_flag = 1
        previous_iban = stored_iban_raw
    elif new_iban_raw and not stored_iban_clean:
        conn.execute(
            "UPDATE vendors SET iban = ?, bic = ?, updated_at = ? WHERE id = ?",
            (new_iban_raw, str(norm.get("vendor_bic") or row["bic"] or ""), now, row["id"]),
        )

    conn.execute(
        """
        UPDATE vendors SET
            address = CASE WHEN ? != '' THEN ? ELSE address END,
            vat_id = CASE WHEN ? != '' THEN ? ELSE vat_id END,
            tax_number = CASE WHEN ? != '' THEN ? ELSE tax_number END,
            payment_terms = CASE WHEN ? != '' THEN ? ELSE payment_terms END,
            vies_status = CASE WHEN ? != 'unchecked' THEN ? ELSE vies_status END,
            vies_checked_at = CASE WHEN ? != '' THEN ? ELSE vies_checked_at END,
            updated_at = ?
        WHERE id = ?
        """,
        (
            str(norm.get("vendor_address") or ""), str(norm.get("vendor_address") or ""),
            str(norm.get("vat_id") or ""), str(norm.get("vat_id") or ""),
            str(norm.get("tax_number") or ""), str(norm.get("tax_number") or ""),
            str(norm.get("payment_terms") or ""), str(norm.get("payment_terms") or ""),
            vies_status, vies_status,
            vies_checked_at, vies_checked_at,
            now,
            row["id"],
        ),
    )
    return iban_change_flag, previous_iban


def _check_duplicate_invoice(
    conn: sqlite3.Connection,
    user_id: str,
    norm: dict,
    file_name: str,
) -> tuple[int, str]:
    vendor_name = str(norm.get("vendor_name") or "").strip()
    total_amount = float(norm.get("total_amount") or 0.0)
    inv_date_str = str(norm.get("invoice_date") or "").strip()

    if not vendor_name or total_amount == 0.0:
        return 0, ""

    u_variants = _user_id_variants(user_id)
    if not u_variants:
        return 0, ""

    placeholders = ",".join("?" for _ in u_variants)
    rows = conn.execute(
        f"""
        SELECT invoice_number, file_name, invoice_date, total_amount
        FROM invoices
        WHERE user_id IN ({placeholders})
          AND lower(vendor_name) = lower(?)
          AND ABS(total_amount - ?) < 0.01
        """,
        [*u_variants, vendor_name, total_amount],
    ).fetchall()

    try:
        target_dt = datetime.strptime(inv_date_str, "%Y-%m-%d").date() if re.match(r"^\d{4}-\d{2}-\d{2}$", inv_date_str) else None
    except Exception:
        target_dt = None

    for r in rows:
        other_file = str(r["file_name"] or "")
        other_num = str(r["invoice_number"] or "")
        if other_file == file_name:
            continue
        other_date_str = str(r["invoice_date"] or "")
        if target_dt and re.match(r"^\d{4}-\d{2}-\d{2}$", other_date_str):
            try:
                other_dt = datetime.strptime(other_date_str, "%Y-%m-%d").date()
                if abs((target_dt - other_dt).days) <= 7:
                    return 1, other_num or other_file
            except Exception:
                pass
        elif other_date_str == inv_date_str:
            return 1, other_num or other_file

    return 0, ""


def _row_to_invoice_dict(row: Dict[str, Any], include_ids: bool = True) -> Dict[str, Any]:
    items = []
    raw_items = row.get("items")
    if raw_items:
        try:
            items = json.loads(raw_items) if isinstance(raw_items, str) else raw_items
        except Exception:
            items = []

    vat_breakdown = []
    raw_vb = row.get("vat_breakdown")
    if raw_vb:
        try:
            vat_breakdown = json.loads(raw_vb) if isinstance(raw_vb, str) else raw_vb
        except Exception:
            vat_breakdown = []

    validation_report = {}
    raw_vr = row.get("validation_report")
    if raw_vr:
        try:
            validation_report = json.loads(raw_vr) if isinstance(raw_vr, str) else raw_vr
        except Exception:
            validation_report = {}

    inv_num = row.get("invoice_number") or "N/A"
    uid = str(row.get("user_id") or "")
    doc_id = row.get("doc_id")
    if not doc_id:
        clean_fb = str(inv_num or row.get("id")).strip().replace(" ", "_").replace("/", "_").replace("\\", "_")
        doc_id = f"inv_{uid}_{clean_fb}"

    tax_val = float(row.get("tax_amount") if row.get("tax_amount") is not None else (row.get("tax") or 0.0))
    subtotal_val = float(row.get("subtotal") or 0.0)

    res = {
        "id": row.get("id"),
        "doc_id": doc_id,
        "file_name": row.get("file_name") or "",
        "vendor_name": row.get("vendor_name") or "Unknown Vendor",
        "vendor_address": row.get("vendor_address") or "",
        "vat_id": row.get("vat_id") or "",
        "tax_number": row.get("tax_number") or "",
        "vendor_iban": row.get("vendor_iban") or "",
        "vendor_bic": row.get("vendor_bic") or "",
        "buyer_name": row.get("buyer_name") or "N/A",
        "buyer_address": row.get("buyer_address") or "",
        "buyer_vat_id": row.get("buyer_vat_id") or "",
        "leitweg_id": row.get("leitweg_id") or "",
        "invoice_number": inv_num,
        "document_type": row.get("document_type") or "invoice",
        "invoice_date": row.get("invoice_date") or "N/A",
        "delivery_date": row.get("delivery_date") or "",
        "due_date": row.get("due_date") or "",
        "payment_terms": row.get("payment_terms") or "",
        "skonto_terms": row.get("skonto_terms") or "",
        "skonto_percent": float(row.get("skonto_percent") or 0.0),
        "skonto_days": int(row.get("skonto_days") or 0),
        "skonto_deadline": row.get("skonto_deadline") or "",
        "skonto_amount": float(row.get("skonto_amount") or 0.0),
        "items": items,
        "line_items": items,
        "vat_breakdown": vat_breakdown,
        "subtotal": subtotal_val,
        "tax": tax_val,
        "tax_amount": tax_val,
        "discount": float(row.get("discount") or 0.0),
        "total_amount": float(row.get("total_amount") or 0.0),
        "currency": row.get("currency") or "EUR",
        "source_currency": row.get("source_currency") or (row.get("currency") or "EUR"),
        "fx_rate": float(row.get("fx_rate") if row.get("fx_rate") is not None else 1.0),
        "fx_rate_date": row.get("fx_rate_date") or "",
        "fx_rate_source": row.get("fx_rate_source") or "",
        "tax_treatment": row.get("tax_treatment") or "standard",
        "original_net": float(row.get("original_net") or 0.0),
        "original_vat": float(row.get("original_vat") or 0.0),
        "original_gross": float(row.get("original_gross") or 0.0),
        "supplier_country": row.get("supplier_country") or "",
        "payment_status": row.get("payment_status") or "Unpaid",
        "validation_report": validation_report,
        "validation_status": row.get("validation_status") or "PENDING",
        "vies_status": row.get("vies_status") or "unchecked",
        "vies_checked_at": row.get("vies_checked_at") or "",
        "vies_details": row.get("vies_details") or "",
        "iban_change_flag": int(row.get("iban_change_flag") or 0),
        "previous_iban": row.get("previous_iban") or "",
        "duplicate_flag": int(row.get("duplicate_flag") or 0),
        "duplicate_of": row.get("duplicate_of") or "",
        "datev_booked": int(row.get("datev_booked") or 0),
        "datev_account": row.get("datev_account") or "",
        "payment_run_id": row.get("payment_run_id") or "",
        "uploaded_at": str(row.get("uploaded_at") or row.get("created_at") or ""),
        "created_at": str(row.get("created_at") or ""),
    }
    if include_ids:
        res["_doc_id"] = doc_id
    return res


def get_all_invoices(
    index=None,
    index_path: str = DEFAULT_CHROMA_PATH,
    user_id: Optional[str] = None,
    include_ids: bool = True,
) -> List[Dict[str, Any]]:
    init_db(CHAT_DB_PATH)
    u_variants = _user_id_variants(user_id)
    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        if u_variants:
            placeholders = ",".join("?" for _ in u_variants)
            rows = conn.execute(
                f"SELECT * FROM invoices WHERE user_id IN ({placeholders}) ORDER BY id DESC",
                u_variants,
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM invoices ORDER BY id DESC").fetchall()
        return [_row_to_invoice_dict(dict(r), include_ids=include_ids) for r in rows]
    finally:
        conn.close()


def get_all_invoices_with_ids(
    index_path: str = DEFAULT_CHROMA_PATH,
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return get_all_invoices(index_path=index_path, user_id=user_id, include_ids=True)


def get_invoice_by_identifier(identifier: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    init_db(CHAT_DB_PATH)
    if not identifier:
        return None
    ident = str(identifier).strip()
    u_variants = _user_id_variants(user_id)

    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        user_clause = ""
        params: List[Any] = []
        if u_variants:
            placeholders = ",".join("?" for _ in u_variants)
            user_clause = f"AND user_id IN ({placeholders})"
            params.extend(u_variants)

        clean_ident = ident
        if clean_ident.startswith("inv_"):
            parts = clean_ident.split("_", 2)
            if len(parts) == 3:
                clean_ident = parts[2]

        sql = f"""
            SELECT * FROM invoices
            WHERE (
                doc_id = ?
                OR invoice_number = ?
                OR file_name = ?
                OR replace(invoice_number, '/', '_') = ?
                OR CAST(id AS TEXT) = ?
            )
            {user_clause}
            ORDER BY id DESC
            LIMIT 1
        """
        row = conn.execute(sql, [ident, ident, ident, clean_ident, ident, *params]).fetchone()
        return _row_to_invoice_dict(dict(row), include_ids=True) if row else None
    finally:
        conn.close()


def get_all_invoices_df(
    index=None,
    index_path: str = DEFAULT_CHROMA_PATH,
    user_id: Optional[str] = None,
) -> pd.DataFrame:
    invoices = get_all_invoices(index=index, index_path=index_path, user_id=user_id)
    cols = [
        "invoice_number", "vendor_name", "buyer_name", "vat_id",
        "invoice_date", "due_date", "total_amount", "subtotal", "tax_amount", "tax",
        "discount", "currency", "payment_status", "validation_status", "uploaded_at"
    ]
    if not invoices:
        return pd.DataFrame(columns=cols)

    records = []
    for inv in invoices:
        records.append({
            "invoice_number": str(inv.get("invoice_number") or "").strip(),
            "vendor_name": str(inv.get("vendor_name") or "").strip(),
            "buyer_name": str(inv.get("buyer_name") or "").strip(),
            "vat_id": str(inv.get("vat_id") or "").strip(),
            "invoice_date": str(inv.get("invoice_date") or "").strip(),
            "due_date": str(inv.get("due_date") or "").strip(),
            "total_amount": float(inv.get("total_amount") or 0.0),
            "subtotal": float(inv.get("subtotal") or 0.0),
            "tax_amount": float(inv.get("tax_amount") or 0.0),
            "tax": float(inv.get("tax_amount") or 0.0),
            "discount": float(inv.get("discount") or 0.0),
            "currency": str(inv.get("currency") or "EUR").strip(),
            "payment_status": str(inv.get("payment_status") or "Unpaid").strip(),
            "validation_status": str(inv.get("validation_status") or "PENDING").strip(),
            "uploaded_at": str(inv.get("uploaded_at") or "").strip(),
        })
    return pd.DataFrame(records)


def get_all_invoices_matrix(
    index=None,
    index_path: str = DEFAULT_CHROMA_PATH,
    user_id: Optional[str] = None,
) -> str:
    df = get_all_invoices_df(index=index, index_path=index_path, user_id=_resolve_user_id(user_id))
    if df.empty:
        return "No invoices found in storage."
    return df.to_markdown(index=False)


def search_invoices_fts(
    query: str,
    user_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    init_db(CHAT_DB_PATH)
    q = str(query or "").strip()
    if not q:
        return get_all_invoices(user_id=user_id)[:limit]

    u_variants = _user_id_variants(user_id)
    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        tokens = [
            t for t in re.findall(r"[\wÄÖÜäöüß-]+", q, flags=re.UNICODE)
            if len(t) >= 2 and t.lower() not in (
                "der", "die", "das", "und", "oder", "von", "für", "mit", "bei", "wie", "was",
                "the", "and", "for", "from", "with", "show", "find", "list", "all", "invoices",
                "rechnung", "rechnungen", "invoice"
            )
        ]
        matched_rows = []
        if tokens:
            fts_expr = " OR ".join(f'"{t.replace(chr(34), "")}"*' for t in tokens)
            if u_variants:
                placeholders = ",".join("?" for _ in u_variants)
                sql = f"""
                    SELECT i.*, bm25(invoices_fts) AS rank
                    FROM invoices_fts
                    JOIN invoices i ON invoices_fts.doc_id = i.doc_id
                    WHERE i.user_id IN ({placeholders})
                      AND invoices_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                matched_rows = conn.execute(sql, [*u_variants, fts_expr, limit]).fetchall()
            else:
                sql = """
                    SELECT i.*, bm25(invoices_fts) AS rank
                    FROM invoices_fts
                    JOIN invoices i ON invoices_fts.doc_id = i.doc_id
                    WHERE invoices_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                matched_rows = conn.execute(sql, [fts_expr, limit]).fetchall()

        if not matched_rows and tokens:
            like_clauses = []
            like_params = []
            for t in tokens:
                like_clauses.append("(lower(vendor_name) LIKE ? OR lower(invoice_number) LIKE ? OR lower(items) LIKE ?)")
                pat = f"%{t.lower()}%"
                like_params.extend([pat, pat, pat])
            where_like = " OR ".join(like_clauses)
            if u_variants:
                placeholders = ",".join("?" for _ in u_variants)
                sql = f"SELECT * FROM invoices WHERE user_id IN ({placeholders}) AND ({where_like}) ORDER BY id DESC LIMIT ?"
                matched_rows = conn.execute(sql, [*u_variants, *like_params, limit]).fetchall()
            else:
                sql = f"SELECT * FROM invoices WHERE ({where_like}) ORDER BY id DESC LIMIT ?"
                matched_rows = conn.execute(sql, [*like_params, limit]).fetchall()

        return [_row_to_invoice_dict(dict(r), include_ids=True) for r in matched_rows]
    except Exception as exc:
        logger.warning("FTS5 search fallback triggered for '%s': %s", q, exc)
        return []
    finally:
        conn.close()


def save_invoice_to_vector_store(
    structured_json: dict,
    file_path: str,
    index_path: str = DEFAULT_CHROMA_PATH,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    init_db(CHAT_DB_PATH)
    user_id_str = _resolve_user_id(user_id) or "default_user"
    u_clean = str(user_id_str).replace("-", "")

    file_name = os.path.basename(file_path) if file_path else "invoice.pdf"
    norm = normalize_invoice_data(structured_json, file_name=file_name)

    uploaded_at = datetime.now(timezone.utc).isoformat()
    norm["uploaded_at"] = uploaded_at

    inv_num = norm["invoice_number"]
    if inv_num and str(inv_num).strip() not in ("N/A", "Unknown", ""):
        clean_inv = str(inv_num).strip().replace(" ", "_").replace("/", "_").replace("\\", "_")
        doc_id = f"inv_{user_id_str}_{clean_inv}"
    else:
        doc_id = f"inv_{uuid.uuid4().hex[:12]}"

    from vies_service import check_vat_vies
    vies_res = check_vat_vies(norm.get("vat_id") or "")
    norm["vies_status"] = vies_res["status"]
    norm["vies_checked_at"] = vies_res["checked_at"]
    norm["vies_details"] = vies_res.get("details", "")

    from validator import validate_invoice
    val_report = validate_invoice(norm)
    val_status = val_report.get("overall_status", "PASS")

    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        iban_flag, prev_iban = _check_and_sync_vendor_iban(
            conn,
            u_clean,
            norm,
            vies_status=norm["vies_status"],
            vies_checked_at=norm["vies_checked_at"],
        )
        dup_flag, dup_of = _check_duplicate_invoice(conn, u_clean, norm, file_name)

        conn.execute(
            """
            INSERT INTO invoices (
                doc_id, user_id, file_name, vendor_name, vendor_address,
                vat_id, tax_number, vendor_iban, vendor_bic,
                buyer_name, buyer_address, buyer_vat_id, leitweg_id,
                invoice_number, document_type, invoice_date, delivery_date, due_date,
                payment_terms, skonto_terms, skonto_percent, skonto_days, skonto_deadline, skonto_amount,
                items, vat_breakdown, subtotal, tax, tax_amount, discount, total_amount,
                currency, payment_status, validation_report, validation_status,
                vies_status, vies_checked_at, vies_details,
                iban_change_flag, previous_iban, duplicate_flag, duplicate_of,
                source_currency, fx_rate, fx_rate_date, fx_rate_source, tax_treatment,
                original_net, original_vat, original_gross, supplier_country,
                uploaded_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?
            )
            ON CONFLICT(user_id, file_name) DO UPDATE SET
                doc_id=excluded.doc_id,
                vendor_name=excluded.vendor_name,
                vendor_address=excluded.vendor_address,
                vat_id=excluded.vat_id,
                tax_number=excluded.tax_number,
                vendor_iban=excluded.vendor_iban,
                vendor_bic=excluded.vendor_bic,
                buyer_name=excluded.buyer_name,
                buyer_address=excluded.buyer_address,
                buyer_vat_id=excluded.buyer_vat_id,
                leitweg_id=excluded.leitweg_id,
                invoice_number=excluded.invoice_number,
                document_type=excluded.document_type,
                invoice_date=excluded.invoice_date,
                delivery_date=excluded.delivery_date,
                due_date=excluded.due_date,
                payment_terms=excluded.payment_terms,
                skonto_terms=excluded.skonto_terms,
                skonto_percent=excluded.skonto_percent,
                skonto_days=excluded.skonto_days,
                skonto_deadline=excluded.skonto_deadline,
                skonto_amount=excluded.skonto_amount,
                items=excluded.items,
                vat_breakdown=excluded.vat_breakdown,
                subtotal=excluded.subtotal,
                tax=excluded.tax,
                tax_amount=excluded.tax_amount,
                discount=excluded.discount,
                total_amount=excluded.total_amount,
                currency=excluded.currency,
                payment_status=excluded.payment_status,
                validation_report=excluded.validation_report,
                validation_status=excluded.validation_status,
                vies_status=excluded.vies_status,
                vies_checked_at=excluded.vies_checked_at,
                vies_details=excluded.vies_details,
                iban_change_flag=excluded.iban_change_flag,
                previous_iban=excluded.previous_iban,
                duplicate_flag=excluded.duplicate_flag,
                duplicate_of=excluded.duplicate_of,
                source_currency=excluded.source_currency,
                fx_rate=excluded.fx_rate,
                fx_rate_date=excluded.fx_rate_date,
                fx_rate_source=excluded.fx_rate_source,
                tax_treatment=excluded.tax_treatment,
                original_net=excluded.original_net,
                original_vat=excluded.original_vat,
                original_gross=excluded.original_gross,
                supplier_country=excluded.supplier_country,
                uploaded_at=excluded.uploaded_at
            """,
            (
                doc_id,
                u_clean,
                file_name,
                str(norm.get("vendor_name") or "").strip(),
                str(norm.get("vendor_address") or "").strip(),
                str(norm.get("vat_id") or "").strip(),
                str(norm.get("tax_number") or "").strip(),
                str(norm.get("vendor_iban") or "").strip(),
                str(norm.get("vendor_bic") or "").strip(),
                str(norm.get("buyer_name") or "").strip(),
                str(norm.get("buyer_address") or "").strip(),
                str(norm.get("buyer_vat_id") or "").strip(),
                str(norm.get("leitweg_id") or "").strip(),
                str(inv_num).strip(),
                str(norm.get("document_type") or "invoice"),
                str(norm.get("invoice_date") or "").strip(),
                str(norm.get("delivery_date") or "").strip(),
                str(norm.get("due_date") or "").strip(),
                str(norm.get("payment_terms") or "").strip(),
                str(norm.get("skonto_terms") or "").strip(),
                float(norm.get("skonto_percent") or 0.0),
                int(norm.get("skonto_days") or 0),
                str(norm.get("skonto_deadline") or "").strip(),
                float(norm.get("skonto_amount") or 0.0),
                json.dumps(norm.get("line_items", []), ensure_ascii=False),
                json.dumps(norm.get("vat_breakdown", []), ensure_ascii=False),
                float(norm.get("subtotal") or 0.0),
                float(norm.get("tax_amount") or 0.0),
                float(norm.get("tax_amount") or 0.0),
                float(norm.get("discount") or 0.0),
                float(norm.get("total_amount") or 0.0),
                str(norm.get("currency") or "EUR").strip(),
                str(norm.get("payment_status") or "Unpaid").strip(),
                json.dumps(val_report, ensure_ascii=False),
                val_status,
                norm["vies_status"],
                norm["vies_checked_at"],
                norm["vies_details"],
                iban_flag,
                prev_iban,
                dup_flag,
                dup_of,
                norm.get("source_currency", "EUR"),
                float(norm.get("fx_rate") or 1.0),
                norm.get("fx_rate_date", ""),
                norm.get("fx_rate_source", ""),
                norm.get("tax_treatment", "standard"),
                float(norm.get("original_net") or 0.0),
                float(norm.get("original_vat") or 0.0),
                float(norm.get("original_gross") or 0.0),
                norm.get("supplier_country", ""),
                uploaded_at,
            ),
        )
        _sync_fts_row(
            conn,
            doc_id=doc_id,
            user_id=u_clean,
            vendor_name=norm.get("vendor_name") or "",
            invoice_number=inv_num,
            line_items=norm.get("line_items") or [],
        )
        conn.commit()
    finally:
        conn.close()

    norm["_doc_id"] = doc_id
    norm["doc_id"] = doc_id
    norm["validation_report"] = val_report
    norm["validation_status"] = val_status
    norm["iban_change_flag"] = iban_flag
    norm["previous_iban"] = prev_iban
    norm["duplicate_flag"] = dup_flag
    norm["duplicate_of"] = dup_of
    return norm


def update_invoice_record(
    identifier: str,
    updates: Dict[str, Any],
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    existing = get_invoice_by_identifier(identifier, user_id=user_id)
    if not existing:
        return None

    # Strictly validate any edited numeric fields so unparseable strings never become 0
    top_numeric_fields = ("subtotal", "tax_amount", "tax", "total_amount", "discount", "skonto_percent", "skonto_amount")
    clean_updates = dict(updates)
    for nf in top_numeric_fields:
        if nf in clean_updates and clean_updates[nf] is not None:
            clean_updates[nf] = parse_german_decimal_strict(clean_updates[nf], nf)

    for items_key in ("line_items", "items"):
        if items_key in clean_updates and isinstance(clean_updates[items_key], list):
            validated_items = []
            for idx, item in enumerate(clean_updates[items_key], start=1):
                item_copy = dict(item)
                for lnf in ("quantity", "qty", "price_unit", "unit_price", "rate", "discount_percent", "discount", "line_total", "total", "vat_rate"):
                    if lnf in item_copy and item_copy[lnf] is not None:
                        item_copy[lnf] = parse_german_decimal_strict(item_copy[lnf], f"line_items[{idx}].{lnf}")
                validated_items.append(item_copy)
            clean_updates[items_key] = validated_items

    if "vat_breakdown" in clean_updates and isinstance(clean_updates["vat_breakdown"], list):
        validated_vb = []
        for idx, vb in enumerate(clean_updates["vat_breakdown"], start=1):
            vb_copy = dict(vb)
            for vnf in ("rate", "net", "vat"):
                if vnf in vb_copy and vb_copy[vnf] is not None:
                    vb_copy[vnf] = parse_german_decimal_strict(vb_copy[vnf], f"vat_breakdown[{idx}].{vnf}")
            validated_vb.append(vb_copy)
        clean_updates["vat_breakdown"] = validated_vb

    merged = {**existing}
    for k, v in clean_updates.items():
        if v is not None:
            merged[k] = v

    norm = normalize_invoice_data(merged, file_name=existing.get("file_name", ""))
    if "payment_status" in updates:
        norm["payment_status"] = str(updates["payment_status"])
    if "datev_booked" in updates:
        norm["datev_booked"] = int(bool(updates["datev_booked"]))
    else:
        norm["datev_booked"] = existing.get("datev_booked", 0)
    if "datev_account" in updates:
        norm["datev_account"] = str(updates["datev_account"])
    else:
        norm["datev_account"] = existing.get("datev_account", "")

    from vies_service import check_vat_vies
    if updates.get("recheck_vies") or norm.get("vat_id") != existing.get("vat_id"):
        vies_res = check_vat_vies(norm.get("vat_id") or "")
        norm["vies_status"] = vies_res["status"]
        norm["vies_checked_at"] = vies_res["checked_at"]
        norm["vies_details"] = vies_res.get("details", "")
    else:
        norm["vies_status"] = existing.get("vies_status", "unchecked")
        norm["vies_checked_at"] = existing.get("vies_checked_at", "")
        norm["vies_details"] = existing.get("vies_details", "")

    from validator import validate_invoice
    val_report = validate_invoice(norm)
    val_status = val_report.get("overall_status", "PASS")

    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        u_clean = str(_resolve_user_id(user_id) or "").replace("-", "")
        iban_flag, prev_iban = _check_and_sync_vendor_iban(
            conn,
            u_clean,
            norm,
            vies_status=norm["vies_status"],
            vies_checked_at=norm["vies_checked_at"],
        )
        if updates.get("acknowledge_iban_change"):
            iban_flag = 0
            prev_iban = ""
            conn.execute(
                "UPDATE vendors SET iban = ?, bic = ? WHERE lower(vendor_name) = lower(?)",
                (norm.get("vendor_iban") or "", norm.get("vendor_bic") or "", norm.get("vendor_name") or ""),
            )

        conn.execute(
            """
            UPDATE invoices SET
                vendor_name = ?,
                vendor_address = ?,
                vat_id = ?,
                tax_number = ?,
                vendor_iban = ?,
                vendor_bic = ?,
                buyer_name = ?,
                buyer_address = ?,
                buyer_vat_id = ?,
                leitweg_id = ?,
                invoice_number = ?,
                document_type = ?,
                invoice_date = ?,
                delivery_date = ?,
                due_date = ?,
                payment_terms = ?,
                skonto_terms = ?,
                skonto_percent = ?,
                skonto_days = ?,
                skonto_deadline = ?,
                skonto_amount = ?,
                items = ?,
                vat_breakdown = ?,
                subtotal = ?,
                tax = ?,
                tax_amount = ?,
                discount = ?,
                total_amount = ?,
                currency = ?,
                payment_status = ?,
                validation_report = ?,
                validation_status = ?,
                vies_status = ?,
                vies_checked_at = ?,
                vies_details = ?,
                iban_change_flag = ?,
                previous_iban = ?,
                datev_booked = ?,
                datev_account = ?,
                source_currency = ?,
                fx_rate = ?,
                fx_rate_date = ?,
                fx_rate_source = ?,
                tax_treatment = ?,
                original_net = ?,
                original_vat = ?,
                original_gross = ?,
                supplier_country = ?
            WHERE id = ?
            """,
            (
                norm["vendor_name"],
                norm["vendor_address"],
                norm["vat_id"],
                norm["tax_number"],
                norm["vendor_iban"],
                norm["vendor_bic"],
                norm["buyer_name"],
                norm["buyer_address"],
                norm["buyer_vat_id"],
                norm["leitweg_id"],
                norm["invoice_number"],
                norm["document_type"],
                norm["invoice_date"],
                norm["delivery_date"],
                norm["due_date"],
                norm["payment_terms"],
                norm["skonto_terms"],
                float(norm["skonto_percent"] or 0.0),
                int(norm["skonto_days"] or 0),
                norm["skonto_deadline"],
                float(norm["skonto_amount"] or 0.0),
                json.dumps(norm["line_items"], ensure_ascii=False),
                json.dumps(norm["vat_breakdown"], ensure_ascii=False),
                float(norm["subtotal"] or 0.0),
                float(norm["tax_amount"] or 0.0),
                float(norm["tax_amount"] or 0.0),
                float(norm["discount"] or 0.0),
                float(norm["total_amount"] or 0.0),
                norm["currency"],
                norm["payment_status"],
                json.dumps(val_report, ensure_ascii=False),
                val_status,
                norm["vies_status"],
                norm["vies_checked_at"],
                norm["vies_details"],
                iban_flag,
                prev_iban,
                int(norm.get("datev_booked") or 0),
                str(norm.get("datev_account") or ""),
                norm.get("source_currency", "EUR"),
                float(norm.get("fx_rate") or 1.0),
                norm.get("fx_rate_date", ""),
                norm.get("fx_rate_source", ""),
                norm.get("tax_treatment", "standard"),
                float(norm.get("original_net") or 0.0),
                float(norm.get("original_vat") or 0.0),
                float(norm.get("original_gross") or 0.0),
                norm.get("supplier_country", ""),
                existing["id"],
            ),
        )
        _sync_fts_row(
            conn,
            doc_id=existing["doc_id"],
            user_id=u_clean,
            vendor_name=norm["vendor_name"],
            invoice_number=norm["invoice_number"],
            line_items=norm["line_items"],
        )
        conn.commit()
    finally:
        conn.close()

    return get_invoice_by_identifier(str(existing["id"]), user_id=user_id)


def delete_invoice(
    doc_id: str,
    index_path: str = DEFAULT_CHROMA_PATH,
    user_id: Optional[str] = None,
) -> bool:
    init_db(CHAT_DB_PATH)
    existing = get_invoice_by_identifier(doc_id, user_id=user_id)
    if not existing:
        return False

    conn = sqlite3.connect(CHAT_DB_PATH, check_same_thread=False)
    try:
        conn.execute("DELETE FROM invoices WHERE id = ?", (existing["id"],))
        conn.execute("DELETE FROM invoices_fts WHERE doc_id = ?", (existing["doc_id"],))
        conn.commit()
        return True
    except Exception as exc:
        logger.error("Failed to delete invoice %s: %s", doc_id, exc)
        return False
    finally:
        conn.close()
