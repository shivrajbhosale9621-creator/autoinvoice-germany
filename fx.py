"""
fx.py - Foreign Exchange (FX) Conversion Engine using ECB Daily Reference Rates (Task 3).

- Fetches ECB daily reference rates from:
  https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
- Rates are quoted as foreign units per 1 EUR: EUR amount = foreign amount / rate.
- Caches the daily XML rates in memory and in a local JSON cache file (.fx_ecb_cache.json)
  so ECB is fetched at most once per day.
- Converts each line item to EUR (rounded to 2 decimals), sets net total = sum of converted
  line totals (so validator V2 passes), converts VAT per rate, and sets gross = net + VAT.
- Never invents, guesses, or hardcodes FX rates. If ECB is unreachable and no cached rate
  exists, raises FxRateUnavailableError so the user can enter a manual rate.
"""

import json
import logging
import os
import urllib.request
from datetime import date
from typing import Any, Dict, Optional, Tuple
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fx_ecb_cache.json")

# In-memory cache keyed by YYYY-MM-DD fetch date:
# { "2026-11-19": { "rate_date": "2026-11-18", "rates": {"USD": 1.05, "PLN": 4.32, ...} } }
_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}

EU_NON_DE_COUNTRIES = {
    "AT", "BE", "BG", "CY", "CZ", "DK", "EE", "ES", "FI", "FR",
    "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL",
    "PL", "PT", "RO", "SE", "SI", "SK",
}


class FxRateUnavailableError(RuntimeError):
    """Raised when ECB is unreachable and no cached rate exists for the currency."""


def suggest_tax_treatment(supplier_country: str) -> str:
    """Suggest default German VAT treatment based on supplier ISO 2-letter country code."""
    cc = str(supplier_country or "").strip().upper()
    if not cc or cc == "DE":
        return "standard"
    if cc in EU_NON_DE_COUNTRIES:
        return "reverse_charge_13b"
    return "import_no_vat"


def _parse_ecb_xml(xml_bytes: bytes) -> Tuple[str, Dict[str, float]]:
    """Parse ECB eurofxref-daily.xml and return (rate_date, {currency: rate_per_eur})."""
    root = ET.fromstring(xml_bytes)
    rate_date = ""
    rates: Dict[str, float] = {"EUR": 1.0}

    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag == "Cube":
            if "time" in elem.attrib and not rate_date:
                rate_date = elem.attrib["time"].strip()
            if "currency" in elem.attrib and "rate" in elem.attrib:
                ccy = elem.attrib["currency"].strip().upper()
                try:
                    rates[ccy] = float(elem.attrib["rate"].strip())
                except ValueError:
                    continue

    if not rate_date or len(rates) <= 1:
        raise FxRateUnavailableError("ECB XML response contained no valid exchange rates.")
    return rate_date, rates


def _load_disk_cache() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(_CACHE_FILE):
        return {}
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_disk_cache(cache_data: Dict[str, Dict[str, Any]]) -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Could not write FX cache file: %s", exc)


def get_ecb_rates(timeout: float = 10.0) -> Tuple[str, Dict[str, float]]:
    """
    Return (rate_date, rates_dict) from today's cache or fetch from ECB once per day.
    Raises FxRateUnavailableError if ECB cannot be reached and no cached entry exists.
    """
    today_str = date.today().isoformat()

    if today_str in _MEMORY_CACHE:
        entry = _MEMORY_CACHE[today_str]
        return entry["rate_date"], dict(entry["rates"])

    disk_cache = _load_disk_cache()
    if today_str in disk_cache:
        entry = disk_cache[today_str]
        if isinstance(entry, dict) and entry.get("rates"):
            _MEMORY_CACHE[today_str] = entry
            return entry.get("rate_date", today_str), dict(entry["rates"])

    try:
        req = urllib.request.Request(
            ECB_DAILY_URL,
            headers={"User-Agent": "AutoInvoice-Germany/3.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_bytes = resp.read()
        rate_date, rates = _parse_ecb_xml(xml_bytes)
        entry = {"rate_date": rate_date, "rates": rates}
        _MEMORY_CACHE[today_str] = entry
        disk_cache[today_str] = entry
        _save_disk_cache(disk_cache)
        return rate_date, rates
    except FxRateUnavailableError:
        raise
    except Exception as exc:
        raise FxRateUnavailableError(
            f"ECB reference rates unreachable ({exc}) and no cached rate exists for {today_str}. "
            "Please enter a manual FX rate (foreign currency units per 1 EUR)."
        ) from exc


def get_ecb_rate_for_currency(currency: str, timeout: float = 10.0) -> Tuple[float, str]:
    """Return (rate_foreign_per_eur, ecb_rate_date) for a given 3-letter currency code."""
    ccy = str(currency or "").strip().upper()
    if ccy == "EUR":
        return 1.0, date.today().isoformat()
    rate_date, rates = get_ecb_rates(timeout=timeout)
    if ccy not in rates:
        raise FxRateUnavailableError(
            f"Currency '{ccy}' is not listed in the ECB daily reference rates ({rate_date}). "
            "Please enter a manual FX rate (foreign currency units per 1 EUR)."
        )
    return float(rates[ccy]), rate_date


def convert_invoice_to_eur(
    inv: Dict[str, Any],
    manual_rate: Optional[float] = None,
    tax_treatment: Optional[str] = None,
    rate_date: Optional[str] = None,
    rate_source: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convert a foreign-currency invoice dictionary to EUR using ECB or manual rate.
    - Preserves original_net, original_vat, original_gross in source_currency.
    - Converts each line item, rounds line_total to 2 decimals, and sets
      subtotal = sum(converted line_totals) so validator V2 passes.
    - Converts VAT breakdown and sets tax_amount = sum(converted VAT) and
      total_amount = round(subtotal + tax_amount, 2).
    """
    from vector_store import parse_numeric

    out = dict(inv)
    src_ccy = str(out.get("source_currency") or out.get("currency") or "EUR").strip().upper()
    if not src_ccy:
        src_ccy = "EUR"
    out["source_currency"] = src_ccy

    if tax_treatment is not None:
        tt = str(tax_treatment).strip()
        if tt in ("standard", "reverse_charge_13b", "import_no_vat"):
            out["tax_treatment"] = tt
    elif not out.get("tax_treatment"):
        out["tax_treatment"] = "standard"

    # Determine original source amounts before any conversion overwrites EUR fields
    already_converted = (str(out.get("currency") or "").upper() == "EUR" and src_ccy != "EUR" and parse_numeric(out.get("original_gross"), 0.0) != 0.0)
    if already_converted:
        orig_net = parse_numeric(out.get("original_net"), 0.0)
        orig_vat = parse_numeric(out.get("original_vat"), 0.0)
        orig_gross = parse_numeric(out.get("original_gross"), 0.0)
    else:
        orig_net = parse_numeric(out.get("original_net") if out.get("original_net") not in (None, 0, 0.0) else out.get("subtotal"), 0.0)
        orig_vat = parse_numeric(out.get("original_vat") if out.get("original_vat") not in (None, 0, 0.0) else (out.get("tax_amount") if out.get("tax_amount") is not None else out.get("tax")), 0.0)
        orig_gross = parse_numeric(out.get("original_gross") if out.get("original_gross") not in (None, 0, 0.0) else out.get("total_amount"), 0.0)
        out["original_net"] = round(orig_net, 2)
        out["original_vat"] = round(orig_vat, 2)
        out["original_gross"] = round(orig_gross, 2)

    if src_ccy == "EUR":
        out["fx_rate"] = 1.0
        out["fx_rate_date"] = out.get("fx_rate_date") or date.today().isoformat()
        out["fx_rate_source"] = out.get("fx_rate_source") or "ECB"
        out["currency"] = "EUR"
        return out

    if manual_rate is not None:
        fx_rate = float(manual_rate)
        if fx_rate <= 0:
            raise ValueError("FX rate must be greater than 0.")
        fx_source = rate_source or "manual"
        fx_date = rate_date or date.today().isoformat()
    else:
        fx_rate, fx_date = get_ecb_rate_for_currency(src_ccy)
        fx_source = rate_source or "ECB"

    out["fx_rate"] = fx_rate
    out["fx_rate_date"] = fx_date
    out["fx_rate_source"] = fx_source
    out["currency"] = "EUR"

    # Convert each line item (store original line_total/unit_price if not yet saved so re-conversion stays exact)
    raw_items = out.get("line_items") or out.get("items") or []
    converted_items = []
    rate_nets: Dict[float, float] = {}

    for it in raw_items:
        item_copy = dict(it)
        orig_lt = parse_numeric(
            item_copy.get("original_line_total")
            if item_copy.get("original_line_total") is not None
            else item_copy.get("line_total", item_copy.get("total")),
            0.0,
        )
        orig_up = parse_numeric(
            item_copy.get("original_unit_price")
            if item_copy.get("original_unit_price") is not None
            else item_copy.get("unit_price", item_copy.get("rate")),
            0.0,
        )
        item_copy["original_line_total"] = round(orig_lt, 2)
        item_copy["original_unit_price"] = orig_up

        conv_lt = round(orig_lt / fx_rate, 2)
        conv_up = round(orig_up / fx_rate, 6)

        item_copy["line_total"] = conv_lt
        item_copy["total"] = conv_lt
        item_copy["unit_price"] = conv_up
        item_copy["rate"] = conv_up

        # Determine effective VAT rate for converted invoice
        v_rate = parse_numeric(item_copy.get("vat_rate"), 0.0)
        if out["tax_treatment"] in ("reverse_charge_13b", "import_no_vat"):
            v_rate = 0.0
            item_copy["vat_rate"] = 0.0
        rate_nets[v_rate] = round(rate_nets.get(v_rate, 0.0) + conv_lt, 2)
        converted_items.append(item_copy)

    out["line_items"] = converted_items
    out["items"] = converted_items

    # Net total = sum of converted line totals so validator V2 passes cleanly
    if converted_items:
        conv_subtotal = round(sum(it["line_total"] for it in converted_items), 2)
    else:
        conv_subtotal = round(orig_net / fx_rate, 2)

    # Convert VAT breakdown
    raw_vb = out.get("vat_breakdown") or []
    converted_vb = []
    if rate_nets:
        # Map converted VAT by rate from original vat_breakdown if available
        orig_vat_by_rate: Dict[float, float] = {}
        for vb in raw_vb:
            r = round(parse_numeric(vb.get("rate"), 0.0), 2)
            v = parse_numeric(vb.get("original_vat") if vb.get("original_vat") is not None else vb.get("vat"), 0.0)
            orig_vat_by_rate[r] = round(orig_vat_by_rate.get(r, 0.0) + v, 2)

        for r, net_sum in sorted(rate_nets.items(), reverse=True):
            if out["tax_treatment"] in ("reverse_charge_13b", "import_no_vat"):
                conv_vat = 0.0
            else:
                ov = orig_vat_by_rate.get(round(r, 2), round(net_sum * fx_rate * (r / 100.0), 2))
                conv_vat = round(ov / fx_rate, 2)
            converted_vb.append({"rate": r, "net": net_sum, "vat": conv_vat})
    elif raw_vb:
        for vb in raw_vb:
            r = parse_numeric(vb.get("rate"), 0.0)
            n_val = parse_numeric(vb.get("original_net") if vb.get("original_net") is not None else vb.get("net"), 0.0)
            v_val = parse_numeric(vb.get("original_vat") if vb.get("original_vat") is not None else vb.get("vat"), 0.0)
            converted_vb.append({
                "rate": r,
                "net": round(n_val / fx_rate, 2),
                "vat": 0.0 if out["tax_treatment"] in ("reverse_charge_13b", "import_no_vat") else round(v_val / fx_rate, 2),
            })
    else:
        converted_vb.append({
            "rate": 0.0 if out["tax_treatment"] in ("reverse_charge_13b", "import_no_vat") else 19.0,
            "net": conv_subtotal,
            "vat": 0.0 if out["tax_treatment"] in ("reverse_charge_13b", "import_no_vat") else round(orig_vat / fx_rate, 2),
        })

    conv_tax = round(sum(vb["vat"] for vb in converted_vb), 2)
    conv_total = round(conv_subtotal + conv_tax, 2)

    out["vat_breakdown"] = converted_vb
    out["subtotal"] = conv_subtotal
    out["tax"] = conv_tax
    out["tax_amount"] = conv_tax
    out["total_amount"] = conv_total

    return out
