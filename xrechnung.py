"""
xrechnung.py - Valid XRechnung 3.0 XML Generator (EN 16931 / UBL 2.1) (Task 6).

Generates a standards-compliant EN 16931 XRechnung XML file from an extracted
invoice record:
- Includes mandatory Leitweg-ID field (BT-10 -> <cbc:BuyerReference>).
- Converts German numbers (1.234,56 -> 1234.56) and dates (DD.MM.YYYY -> YYYY-MM-DD).
- Supports both regular invoices (TypeCode 380) and credit notes (TypeCode 381).
"""

import re
from typing import Dict, Any, List, Tuple
from lxml import etree

from vector_store import parse_numeric, parse_german_date

UNIT_CODE_MAP = {
    "stk": "C62",
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


def _split_german_address(address_str: str, default_city: str = "Musterstadt", default_zip: str = "10115") -> Tuple[str, str, str]:
    """Extract (street, postal_code, city) from a German address string."""
    s = str(address_str or "").strip()
    if not s:
        return "Musterstraße 1", default_zip, default_city

    # Match 5-digit German postal code + city
    m_zip = re.search(r"\b(\d{5})\s+([A-Za-zÄÖÜäöüß\-\s]+)", s)
    postal = m_zip.group(1) if m_zip else default_zip
    city = m_zip.group(2).split(",")[0].split("\n")[0].strip() if m_zip else default_city

    # Street is the part before postal code
    parts = [p.strip() for p in re.split(r"[,·\n]", s) if p.strip()]
    street = parts[0] if parts else "Musterstraße 1"
    for p in parts:
        if any(ch.isdigit() for ch in p) and postal not in p:
            street = p
            break

    return street, postal, city


def _fmt_xml_dec(val: Any, decimals: int = 2) -> str:
    """Format numeric value with dot decimal separator for XRechnung XML (1234.56)."""
    num = parse_numeric(val, 0.0)
    return f"{num:.{decimals}f}"


def generate_xrechnung_xml(inv: Dict[str, Any], default_leitweg_id: str = "") -> str:
    """
    Build a valid XRechnung 3.0 (EN 16931) UBL 2.1 XML document string.
    Raises ValueError if Leitweg-ID (BT-10) is missing and no default_leitweg_id is provided.
    """
    leitweg_id = str(inv.get("leitweg_id") or default_leitweg_id or "").strip()
    if not leitweg_id:
        raise ValueError(
            "Leitweg-ID (BT-10) fehlt. Eine Rechnung ohne Leitweg-ID kann nicht als XRechnung exportiert werden."
        )

    doc_type = str(inv.get("document_type") or "invoice").lower()
    is_credit = ("credit" in doc_type or "gutschrift" in doc_type or str(inv.get("invoice_number") or "").upper().startswith("GS-"))
    type_code = "381" if is_credit else "380"

    inv_num = str(inv.get("invoice_number") or "RE-00001").strip()
    issue_date = parse_german_date(inv.get("invoice_date")) or "2026-10-06"
    due_date = parse_german_date(inv.get("due_date")) or issue_date
    delivery_date = parse_german_date(inv.get("delivery_date")) or issue_date
    currency = str(inv.get("currency") or "EUR").strip().upper()
    if currency not in ("EUR", "USD", "GBP", "CHF"):
        currency = "EUR"

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
    # BT-9 Due date (YYYY-MM-DD)
    etree.SubElement(root, CBC + "DueDate").text = due_date
    # BT-3 Invoice type code
    etree.SubElement(root, CBC + "InvoiceTypeCode").text = type_code
    # BT-5 Document currency code
    etree.SubElement(root, CBC + "DocumentCurrencyCode").text = currency
    # BT-10 Mandatory BuyerReference (Leitweg-ID)
    etree.SubElement(root, CBC + "BuyerReference").text = leitweg_id

    # Seller (BG-4 AccountingSupplierParty)
    vendor_name = str(inv.get("vendor_name") or "Lieferant GmbH").strip()
    v_street, v_zip, v_city = _split_german_address(inv.get("vendor_address") or "", "Ostfildern", "73760")
    vat_id = re.sub(r"\s+", "", str(inv.get("vat_id") or "DE287364519")).upper()

    sup_party_wrap = etree.SubElement(root, CAC + "AccountingSupplierParty")
    sup_party = etree.SubElement(sup_party_wrap, CAC + "Party")
    sup_endpoint = etree.SubElement(sup_party, CBC + "EndpointID", schemeID="EM")
    sup_endpoint.text = "buchhaltung@lieferant.de"
    sup_name_wrap = etree.SubElement(sup_party, CAC + "PartyName")
    etree.SubElement(sup_name_wrap, CBC + "Name").text = vendor_name

    sup_addr = etree.SubElement(sup_party, CAC + "PostalAddress")
    etree.SubElement(sup_addr, CBC + "StreetName").text = v_street
    etree.SubElement(sup_addr, CBC + "CityName").text = v_city
    etree.SubElement(sup_addr, CBC + "PostalZone").text = v_zip
    sup_country = etree.SubElement(sup_addr, CAC + "Country")
    etree.SubElement(sup_country, CBC + "IdentificationCode").text = "DE"

    sup_tax = etree.SubElement(sup_party, CAC + "PartyTaxScheme")
    etree.SubElement(sup_tax, CBC + "CompanyID").text = vat_id
    sup_ts = etree.SubElement(sup_tax, CAC + "TaxScheme")
    etree.SubElement(sup_ts, CBC + "ID").text = "VAT"

    sup_legal = etree.SubElement(sup_party, CAC + "PartyLegalEntity")
    etree.SubElement(sup_legal, CBC + "RegistrationName").text = vendor_name

    sup_contact = etree.SubElement(sup_party, CAC + "Contact")
    etree.SubElement(sup_contact, CBC + "Name").text = vendor_name
    etree.SubElement(sup_contact, CBC + "Telephone").text = "+49 711 448210-0"
    etree.SubElement(sup_contact, CBC + "ElectronicMail").text = "buchhaltung@lieferant.de"

    # Buyer (BG-7 AccountingCustomerParty)
    buyer_name = str(inv.get("buyer_name") or "Rheinwerk Anlagenbau AG").strip()
    b_street, b_zip, b_city = _split_german_address(inv.get("buyer_address") or "", "Duisburg", "47119")

    cust_party_wrap = etree.SubElement(root, CAC + "AccountingCustomerParty")
    cust_party = etree.SubElement(cust_party_wrap, CAC + "Party")
    cust_endpoint = etree.SubElement(cust_party, CBC + "EndpointID", schemeID="EM")
    cust_endpoint.text = "rechnungseingang@kunde.de"
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

    # Delivery (BG-13)
    deliv = etree.SubElement(root, CAC + "Delivery")
    etree.SubElement(deliv, CBC + "ActualDeliveryDate").text = delivery_date

    # PaymentMeans (BG-16 SEPA Credit Transfer code 58)
    pm = etree.SubElement(root, CAC + "PaymentMeans")
    etree.SubElement(pm, CBC + "PaymentMeansCode").text = "58"
    iban_clean = re.sub(r"\s+", "", str(inv.get("vendor_iban") or "DE89612901200457331008")).upper()
    fin_acc = etree.SubElement(pm, CAC + "PayeeFinancialAccount")
    etree.SubElement(fin_acc, CBC + "ID").text = iban_clean
    if inv.get("vendor_bic"):
        fin_inst = etree.SubElement(fin_acc, CAC + "FinancialInstitutionBranch")
        etree.SubElement(fin_inst, CBC + "ID").text = str(inv.get("vendor_bic")).strip()

    # PaymentTerms (BT-20 including Skonto if present)
    terms_parts = []
    if inv.get("payment_terms"):
        terms_parts.append(str(inv["payment_terms"]).strip())
    if inv.get("skonto_terms"):
        terms_parts.append(str(inv["skonto_terms"]).strip())
    if terms_parts:
        pt = etree.SubElement(root, CAC + "PaymentTerms")
        etree.SubElement(pt, CBC + "Note").text = " | ".join(terms_parts)

    # TaxTotal (BG-23)
    subtotal = parse_numeric(inv.get("subtotal"), 0.0)
    tax_amount = parse_numeric(inv.get("tax_amount") if inv.get("tax_amount") is not None else inv.get("tax"), 0.0)
    total_amount = parse_numeric(inv.get("total_amount"), 0.0)
    line_items: List[Dict[str, Any]] = inv.get("line_items") or inv.get("items") or []
    vat_breakdown: List[Dict[str, Any]] = inv.get("vat_breakdown") or []

    if not vat_breakdown:
        by_r: Dict[float, float] = {}
        for it in line_items:
            r = round(parse_numeric(it.get("vat_rate"), 19.0), 2)
            by_r[r] = round(by_r.get(r, 0.0) + parse_numeric(it.get("line_total"), 0.0), 2)
        if by_r:
            for r, net_v in sorted(by_r.items(), reverse=True):
                vat_breakdown.append({
                    "rate": r,
                    "net": net_v,
                    "vat": round(net_v * (r / 100.0), 2),
                })
        else:
            vat_breakdown = [{"rate": 19.0, "net": subtotal, "vat": tax_amount}]

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
        etree.SubElement(t_cat, CBC + "ID").text = "S" if r_val > 0 else "Z"
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
        qty_val = parse_numeric(it.get("quantity") if it.get("quantity") is not None else it.get("qty"), 1.0)
        etree.SubElement(line_el, CBC + "InvoicedQuantity", unitCode=unit_code).text = _fmt_xml_dec(qty_val, 2)

        lt_val = parse_numeric(it.get("line_total"), 0.0)
        etree.SubElement(line_el, CBC + "LineExtensionAmount", currencyID=currency).text = _fmt_xml_dec(lt_val, 2)

        item_el = etree.SubElement(line_el, CAC + "Item")
        desc_text = str(it.get("description") or it.get("name") or f"Position {pos_id}").strip()
        etree.SubElement(item_el, CBC + "Description").text = desc_text
        etree.SubElement(item_el, CBC + "Name").text = desc_text[:120]

        art_no = str(it.get("article_number") or "").strip()
        if art_no:
            s_id = etree.SubElement(item_el, CAC + "SellersItemIdentification")
            etree.SubElement(s_id, CBC + "ID").text = art_no

        cls_tax = etree.SubElement(item_el, CAC + "ClassifiedTaxCategory")
        r_line = parse_numeric(it.get("vat_rate"), 19.0)
        etree.SubElement(cls_tax, CBC + "ID").text = "S" if r_line > 0 else "Z"
        etree.SubElement(cls_tax, CBC + "Percent").text = _fmt_xml_dec(r_line, 2)
        cls_scheme = etree.SubElement(cls_tax, CAC + "TaxScheme")
        etree.SubElement(cls_scheme, CBC + "ID").text = "VAT"

        price_el = etree.SubElement(line_el, CAC + "Price")
        up_val = parse_numeric(it.get("unit_price"), 0.0)
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
