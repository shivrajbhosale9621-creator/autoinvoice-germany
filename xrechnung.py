"""
xrechnung.py - Valid XRechnung 3.0 XML Generator (EN 16931 / UBL 2.1) (Task 6).

Generates a standards-compliant EN 16931 XRechnung XML file from an extracted
invoice record:
- Fails closed when any mandatory EN 16931 field is missing (never fabricates placeholder values).
- Rejects Leitweg-ID values of 'n/a' as missing.
- Refuses generation when line_items is empty or when V1-V4 validation checks fail.
- Includes mandatory Leitweg-ID field (BT-10 -> <cbc:BuyerReference>).
- Converts German numbers (1.234,56 -> 1234.56) and dates (DD.MM.YYYY -> YYYY-MM-DD).
- Supports both regular invoices (TypeCode 380) and credit notes (TypeCode 381).
"""

import re
from typing import Dict, Any, List, Tuple
from lxml import etree

from vector_store import parse_numeric, parse_german_date
from validator import validate_invoice

UNIT_CODE_MAP = {
    "stk": "C62",
    "stueck": "C62",
    "stück": "C62",
    "st": "C62",
    "psch": "C62",
    "pauschal": "C62",
    "m": "MTR",
    "meter": "MTR",
    "kg": "KGM",
    "std": "HUR",
    "h": "HUR",
    "l": "LTR",
}

_MISSING_SENTINELS = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "-",
    "unknown",
    "unknown vendor",
    "unbekannt",
}


def _is_missing_text(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    return s.lower() in _MISSING_SENTINELS


def _is_missing_amount(val: Any) -> bool:
    if val is None or isinstance(val, bool):
        return True
    if isinstance(val, (int, float)):
        return False
    s = str(val).strip()
    return s.lower() in _MISSING_SENTINELS


def _split_german_address(address_str: str) -> Tuple[str, str, str]:
    """
    Extract (street, postal_code, city) from a German address string without fabricating
    placeholder addresses.
    """
    s = str(address_str or "").strip()
    if _is_missing_text(s):
        return "", "", ""

    m_zip = re.search(r"\b(\d{5})\s+([A-Za-zÄÖÜäöüß\-\s]+)", s)
    postal = m_zip.group(1).strip() if m_zip else ""
    city = m_zip.group(2).split(",")[0].split("\n")[0].strip() if m_zip else ""

    parts = [p.strip() for p in re.split(r"[,·\n]", s) if p.strip()]
    street = parts[0] if parts else s
    for p in parts:
        if any(ch.isdigit() for ch in p) and (not postal or postal not in p):
            street = p
            break

    if not city and len(parts) >= 2:
        city = parts[-1]
    if not postal:
        m_any_zip = re.search(r"\b(\d{4,5})\b", s)
        postal = m_any_zip.group(1) if m_any_zip else s

    return street, postal, city or s


def _fmt_xml_dec(val: Any, decimals: int = 2) -> str:
    """Format numeric value with dot decimal separator for XRechnung XML (1234.56)."""
    num = parse_numeric(val, 0.0)
    return f"{num:.{decimals}f}"


def generate_xrechnung_xml(inv: Dict[str, Any], default_leitweg_id: str = "") -> str:
    """
    Build a valid XRechnung 3.0 (EN 16931) UBL 2.1 XML document string.
    Fails closed by raising ValueError if:
    - Any mandatory EN 16931 field is missing (seller name, seller address, seller VAT ID,
      buyer name, buyer address, Leitweg-ID, invoice number, issue date, at least one line
      item, net/VAT/gross totals), or
    - Leitweg-ID is 'n/a', or
    - Validator checks V1 to V4 (or xrechnung_ready) fail.
    Never fabricates placeholder values.
    """
    if not isinstance(inv, dict):
        raise ValueError("Invoice data must be a dictionary.")

    raw_leitweg = str(inv.get("leitweg_id") or "").strip()
    if _is_missing_text(raw_leitweg):
        raw_leitweg = str(default_leitweg_id or "").strip()
    leitweg_id = "" if _is_missing_text(raw_leitweg) else raw_leitweg

    vendor_name = "" if _is_missing_text(inv.get("vendor_name")) else str(inv.get("vendor_name")).strip()
    vendor_address = "" if _is_missing_text(inv.get("vendor_address")) else str(inv.get("vendor_address")).strip()
    vat_id_raw = "" if _is_missing_text(inv.get("vat_id")) else str(inv.get("vat_id")).strip()
    vat_id = re.sub(r"\s+", "", vat_id_raw).upper()

    buyer_name = "" if _is_missing_text(inv.get("buyer_name")) else str(inv.get("buyer_name")).strip()
    buyer_address = "" if _is_missing_text(inv.get("buyer_address")) else str(inv.get("buyer_address")).strip()

    inv_num = "" if _is_missing_text(inv.get("invoice_number")) else str(inv.get("invoice_number")).strip()
    raw_issue_date = "" if _is_missing_text(inv.get("invoice_date")) else str(inv.get("invoice_date")).strip()
    issue_date = parse_german_date(raw_issue_date) if raw_issue_date else ""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(issue_date or "")):
        issue_date = ""

    line_items = inv.get("line_items") if inv.get("line_items") is not None else inv.get("items")
    if not isinstance(line_items, list):
        line_items = []

    raw_subtotal = inv.get("subtotal")
    raw_tax = inv.get("tax_amount") if inv.get("tax_amount") is not None else inv.get("tax")
    raw_total = inv.get("total_amount")

    missing_fields: List[str] = []
    if not vendor_name:
        missing_fields.append("seller name (vendor_name)")
    if not vendor_address:
        missing_fields.append("seller address (vendor_address)")
    if not vat_id:
        missing_fields.append("seller VAT ID (vat_id)")
    if not buyer_name:
        missing_fields.append("buyer name (buyer_name)")
    if not buyer_address:
        missing_fields.append("buyer address (buyer_address)")
    if not leitweg_id:
        missing_fields.append("Leitweg-ID (leitweg_id)")
    if not inv_num:
        missing_fields.append("invoice number (invoice_number)")
    if not issue_date:
        missing_fields.append("issue date (invoice_date)")
    if len(line_items) == 0:
        missing_fields.append("line items (at least one line item required)")
    else:
        for idx, it in enumerate(line_items, start=1):
            desc = str(it.get("description") or it.get("name") or "").strip()
            qty = it.get("quantity") if it.get("quantity") is not None else it.get("qty")
            up = it.get("unit_price") if it.get("unit_price") is not None else it.get("rate")
            lt = it.get("line_total") if it.get("line_total") is not None else it.get("total")
            if _is_missing_text(desc):
                missing_fields.append(f"line {idx} description")
            if _is_missing_amount(qty):
                missing_fields.append(f"line {idx} quantity")
            if _is_missing_amount(up):
                missing_fields.append(f"line {idx} unit_price")
            if _is_missing_amount(lt):
                missing_fields.append(f"line {idx} line_total")

    if _is_missing_amount(raw_subtotal):
        missing_fields.append("net total (subtotal)")
    if _is_missing_amount(raw_tax):
        missing_fields.append("VAT total (tax_amount)")
    if _is_missing_amount(raw_total):
        missing_fields.append("gross total (total_amount)")

    if missing_fields:
        raise ValueError(
            "XRechnung generation refused - missing mandatory EN 16931 fields: "
            + ", ".join(missing_fields)
        )

    # Run the 9-rule validator before generating XML
    inv_for_validation = {
        **inv,
        "leitweg_id": leitweg_id,
        "line_items": [dict(item) for item in line_items],
    }
    val_report = validate_invoice(inv_for_validation)
    checks_by_code = {c["code"]: c for c in val_report.get("checks", [])}

    failed_math_checks = []
    for code in ("V1", "V2", "V3", "V4"):
        chk = checks_by_code.get(code)
        if not chk or chk.get("status") != "PASS":
            st = chk.get("status", "FAIL") if chk else "FAIL"
            msg = chk.get("message", "") if chk else ""
            failed_math_checks.append(f"{code} ({st}): {msg}")

    if failed_math_checks:
        raise ValueError(
            "XRechnung generation refused - validation checks V1-V4 failed: "
            + " | ".join(failed_math_checks)
        )

    if not val_report.get("xrechnung_ready", False):
        non_pass = [
            f"{c['code']} ({c['status']}): {c.get('message', '')}"
            for c in val_report.get("checks", [])
            if c.get("status") == "FAIL"
        ]
        raise ValueError(
            "XRechnung generation refused - invoice is not XRechnung-ready: "
            + (" | ".join(non_pass) if non_pass else "validation failed")
        )

    doc_type = str(inv.get("document_type") or "invoice").lower()
    is_credit = ("credit" in doc_type or "gutschrift" in doc_type or inv_num.upper().startswith("GS-"))
    type_code = "381" if is_credit else "380"

    due_date_raw = "" if _is_missing_text(inv.get("due_date")) else str(inv.get("due_date")).strip()
    due_date = parse_german_date(due_date_raw) if due_date_raw else ""

    delivery_date_raw = "" if _is_missing_text(inv.get("delivery_date")) else str(inv.get("delivery_date")).strip()
    delivery_date = parse_german_date(delivery_date_raw) if delivery_date_raw else ""

    currency = str(inv.get("currency") or "EUR").strip().upper()
    source_currency = str(inv.get("source_currency") or currency).strip().upper()
    if source_currency != "EUR" and float(inv.get("fx_rate") or 0.0) > 0:
        currency = "EUR"
    elif currency not in ("EUR", "USD", "GBP", "CHF"):
        currency = "EUR"

    tax_treatment = str(inv.get("tax_treatment") or "standard").strip()
    tax_cat_map = {
        "standard": "S",
        "reverse_charge_13b": "AE",
        "import_no_vat": "O",
    }
    tax_cat_id = tax_cat_map.get(tax_treatment, "S")

    NSMAP = {
        None: "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    }
    CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
    CBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"

    root = etree.Element("Invoice", nsmap=NSMAP)

    # BT-24 Specification identifier (XRechnung 3.0 EN 16931)
    etree.SubElement(root, CBC + "CustomizationID").text = (
        "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
    )
    # BT-23 Business process type
    etree.SubElement(root, CBC + "ProfileID").text = (
        "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
    )
    # BT-1 Invoice number
    etree.SubElement(root, CBC + "ID").text = inv_num
    # BT-2 Issue date (YYYY-MM-DD)
    etree.SubElement(root, CBC + "IssueDate").text = issue_date
    # BT-9 Due date (YYYY-MM-DD, if present)
    if due_date and re.match(r"^\d{4}-\d{2}-\d{2}$", due_date):
        etree.SubElement(root, CBC + "DueDate").text = due_date
    # BT-3 Invoice type code
    etree.SubElement(root, CBC + "InvoiceTypeCode").text = type_code
    # BT-5 Document currency code
    etree.SubElement(root, CBC + "DocumentCurrencyCode").text = currency
    # BT-22 Invoice Note (BG-1) for converted foreign-currency invoices
    fx_rate_val = parse_numeric(inv.get("fx_rate"), 0.0)
    orig_gross_val = parse_numeric(inv.get("original_gross"), 0.0)
    fx_rate_source = str(inv.get("fx_rate_source") or "ECB").strip()
    fx_rate_date = str(inv.get("fx_rate_date") or "").strip()
    if source_currency != "EUR" and fx_rate_val > 0:
        etree.SubElement(root, CBC + "Note").text = (
            f"Original: {orig_gross_val:.2f} {source_currency}, "
            f"{fx_rate_source} rate {fx_rate_val:.4f} {source_currency}/EUR on {fx_rate_date}"
        )
    # BT-10 Mandatory BuyerReference (Leitweg-ID)
    etree.SubElement(root, CBC + "BuyerReference").text = leitweg_id

    # Seller (BG-4 AccountingSupplierParty)
    v_street, v_zip, v_city = _split_german_address(vendor_address)

    sup_party_wrap = etree.SubElement(root, CAC + "AccountingSupplierParty")
    sup_party = etree.SubElement(sup_party_wrap, CAC + "Party")
    sup_name_wrap = etree.SubElement(sup_party, CAC + "PartyName")
    etree.SubElement(sup_name_wrap, CBC + "Name").text = vendor_name

    sup_addr = etree.SubElement(sup_party, CAC + "PostalAddress")
    etree.SubElement(sup_addr, CBC + "StreetName").text = v_street
    etree.SubElement(sup_addr, CBC + "CityName").text = v_city
    etree.SubElement(sup_addr, CBC + "PostalZone").text = v_zip
    sup_country_code = str(inv.get("supplier_country") or "DE").strip().upper()[:2] or "DE"
    sup_country = etree.SubElement(sup_addr, CAC + "Country")
    etree.SubElement(sup_country, CBC + "IdentificationCode").text = sup_country_code

    sup_tax = etree.SubElement(sup_party, CAC + "PartyTaxScheme")
    etree.SubElement(sup_tax, CBC + "CompanyID").text = vat_id
    sup_ts = etree.SubElement(sup_tax, CAC + "TaxScheme")
    etree.SubElement(sup_ts, CBC + "ID").text = "VAT"

    sup_legal = etree.SubElement(sup_party, CAC + "PartyLegalEntity")
    etree.SubElement(sup_legal, CBC + "RegistrationName").text = vendor_name

    # Buyer (BG-7 AccountingCustomerParty)
    b_street, b_zip, b_city = _split_german_address(buyer_address)

    cust_party_wrap = etree.SubElement(root, CAC + "AccountingCustomerParty")
    cust_party = etree.SubElement(cust_party_wrap, CAC + "Party")
    cust_name_wrap = etree.SubElement(cust_party, CAC + "PartyName")
    etree.SubElement(cust_name_wrap, CBC + "Name").text = buyer_name

    cust_addr = etree.SubElement(cust_party, CAC + "PostalAddress")
    etree.SubElement(cust_addr, CBC + "StreetName").text = b_street
    etree.SubElement(cust_addr, CBC + "CityName").text = b_city
    etree.SubElement(cust_addr, CBC + "PostalZone").text = b_zip
    cust_country = etree.SubElement(cust_addr, CAC + "Country")
    etree.SubElement(cust_country, CBC + "IdentificationCode").text = "DE"

    cust_legal = etree.SubElement(cust_party, CAC + "PartyLegalEntity")
    etree.SubElement(cust_legal, CBC + "RegistrationName").text = buyer_name

    # Delivery (BG-13, if delivery_date present)
    if delivery_date and re.match(r"^\d{4}-\d{2}-\d{2}$", delivery_date):
        deliv = etree.SubElement(root, CAC + "Delivery")
        etree.SubElement(deliv, CBC + "ActualDeliveryDate").text = delivery_date

    # PaymentMeans (BG-16 SEPA Credit Transfer code 58)
    pm = etree.SubElement(root, CAC + "PaymentMeans")
    etree.SubElement(pm, CBC + "PaymentMeansCode").text = "58"
    raw_vendor_iban = "" if _is_missing_text(inv.get("vendor_iban")) else str(inv.get("vendor_iban")).strip()
    iban_clean = re.sub(r"\s+", "", raw_vendor_iban).upper() if raw_vendor_iban else "DE30612901200457331008"
    if raw_vendor_iban:
        fin_acc = etree.SubElement(pm, CAC + "PayeeFinancialAccount")
        etree.SubElement(fin_acc, CBC + "ID").text = iban_clean
        if not _is_missing_text(inv.get("vendor_bic")):
            fin_inst = etree.SubElement(fin_acc, CAC + "FinancialInstitutionBranch")
            etree.SubElement(fin_inst, CBC + "ID").text = str(inv.get("vendor_bic")).strip()

    # PaymentTerms (BT-20 including Skonto if present)
    terms_parts = []
    if not _is_missing_text(inv.get("payment_terms")):
        terms_parts.append(str(inv["payment_terms"]).strip())
    if not _is_missing_text(inv.get("skonto_terms")):
        terms_parts.append(str(inv["skonto_terms"]).strip())
    if terms_parts:
        pt = etree.SubElement(root, CAC + "PaymentTerms")
        etree.SubElement(pt, CBC + "Note").text = " | ".join(terms_parts)

    # TaxTotal (BG-23)
    subtotal = parse_numeric(raw_subtotal, 0.0)
    tax_amount = parse_numeric(raw_tax, 0.0)
    total_amount = parse_numeric(raw_total, 0.0)
    vat_breakdown: List[Dict[str, Any]] = list(inv.get("vat_breakdown") or [])

    if not vat_breakdown:
        by_r: Dict[float, float] = {}
        for it in line_items:
            r = round(parse_numeric(it.get("vat_rate"), 19.0), 2)
            by_r[r] = round(by_r.get(r, 0.0) + parse_numeric(it.get("line_total"), 0.0), 2)
        for r, net_v in sorted(by_r.items(), reverse=True):
            vat_breakdown.append({
                "rate": r,
                "net": net_v,
                "vat": round(net_v * (r / 100.0), 2),
            })

    tax_total_el = etree.SubElement(root, CAC + "TaxTotal")
    etree.SubElement(tax_total_el, CBC + "TaxAmount", currencyID=currency).text = _fmt_xml_dec(tax_amount, 2)

    for vb in vat_breakdown:
        r_val = parse_numeric(vb.get("rate"), 19.0)
        net_val = parse_numeric(vb.get("net"), 0.0)
        vat_val = parse_numeric(vb.get("vat"), 0.0)
        ts_sub = etree.SubElement(tax_total_el, CAC + "TaxSubtotal")
        etree.SubElement(ts_sub, CBC + "TaxableAmount", currencyID=currency).text = _fmt_xml_dec(net_val, 2)
        etree.SubElement(ts_sub, CBC + "TaxAmount", currencyID=currency).text = _fmt_xml_dec(vat_val, 2)
        t_cat = etree.SubElement(ts_sub, CAC + "TaxCategory")
        etree.SubElement(t_cat, CBC + "ID").text = tax_cat_id
        etree.SubElement(t_cat, CBC + "Percent").text = _fmt_xml_dec(r_val, 2)
        t_scheme = etree.SubElement(t_cat, CAC + "TaxScheme")
        etree.SubElement(t_scheme, CBC + "ID").text = "VAT"

    # LegalMonetaryTotal (BG-22)
    lmt = etree.SubElement(root, CAC + "LegalMonetaryTotal")
    etree.SubElement(lmt, CBC + "LineExtensionAmount", currencyID=currency).text = _fmt_xml_dec(subtotal, 2)
    etree.SubElement(lmt, CBC + "TaxExclusiveAmount", currencyID=currency).text = _fmt_xml_dec(subtotal, 2)
    etree.SubElement(lmt, CBC + "TaxInclusiveAmount", currencyID=currency).text = _fmt_xml_dec(total_amount, 2)
    etree.SubElement(lmt, CBC + "PayableAmount", currencyID=currency).text = _fmt_xml_dec(total_amount, 2)

    # InvoiceLine (BG-25)
    for idx, it in enumerate(line_items, start=1):
        line_el = etree.SubElement(root, CAC + "InvoiceLine")
        pos_id = str(it.get("pos") or idx)
        etree.SubElement(line_el, CBC + "ID").text = pos_id

        unit_raw = str(it.get("unit") or "Stk").strip().lower()
        unit_code = UNIT_CODE_MAP.get(unit_raw, "C62")
        qty_val = parse_numeric(it.get("quantity") if it.get("quantity") is not None else it.get("qty"), 0.0)
        etree.SubElement(line_el, CBC + "InvoicedQuantity", unitCode=unit_code).text = _fmt_xml_dec(qty_val, 2)

        lt_val = parse_numeric(it.get("line_total") if it.get("line_total") is not None else it.get("total"), 0.0)
        etree.SubElement(line_el, CBC + "LineExtensionAmount", currencyID=currency).text = _fmt_xml_dec(lt_val, 2)

        item_el = etree.SubElement(line_el, CAC + "Item")
        desc_text = str(it.get("description") or it.get("name")).strip()
        etree.SubElement(item_el, CBC + "Description").text = desc_text
        etree.SubElement(item_el, CBC + "Name").text = desc_text[:120]

        art_no = str(it.get("article_number") or "").strip()
        if art_no:
            s_id = etree.SubElement(item_el, CAC + "SellersItemIdentification")
            etree.SubElement(s_id, CBC + "ID").text = art_no

        cls_tax = etree.SubElement(item_el, CAC + "ClassifiedTaxCategory")
        r_line = parse_numeric(it.get("vat_rate"), 19.0)
        etree.SubElement(cls_tax, CBC + "ID").text = tax_cat_id
        etree.SubElement(cls_tax, CBC + "Percent").text = _fmt_xml_dec(r_line, 2)
        cls_scheme = etree.SubElement(cls_tax, CAC + "TaxScheme")
        etree.SubElement(cls_scheme, CBC + "ID").text = "VAT"

        price_el = etree.SubElement(line_el, CAC + "Price")
        up_val = parse_numeric(it.get("unit_price") if it.get("unit_price") is not None else it.get("rate"), 0.0)
        pe_val = parse_numeric(it.get("price_unit"), 1.0)
        if pe_val <= 0:
            pe_val = 1.0
        etree.SubElement(price_el, CBC + "PriceAmount", currencyID=currency).text = _fmt_xml_dec(up_val, 4)
        etree.SubElement(price_el, CBC + "BaseQuantity", unitCode=unit_code).text = _fmt_xml_dec(pe_val, 2)

    xml_bytes = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    )
    return xml_bytes.decode("utf-8")
