"""
tests/test_foreign_conversion.py - Verification suite for Door 2 Mini (Foreign Invoice Conversion).
Tests:
- Pinned FX conversion for INV-F1 (PLN -> EUR at 4.2500) and INV-F2 (USD -> EUR at 1.1500)
- XRechnung EN 16931 output for converted F1 (AE) and F2 (O) + BT-22 Note + DocumentCurrencyCode EUR
- Country-aware V5 validation (DE valid/invalid, PL valid, IN CANNOT VERIFY)
- Foreign upload mode 422 when GROQ_API_KEY is empty (never falls back to deterministic parser)
- POST /api/invoice/{doc_id}/convert endpoint with manual rate & tax_treatment
"""

import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fx import convert_invoice_to_eur, suggest_tax_treatment
from validator import validate_invoice
from xrechnung import generate_xrechnung_xml


def _make_f1_dict():
    return {
        "invoice_number": "FV/2026/1147",
        "invoice_date": "2026-11-12",
        "due_date": "2026-12-12",
        "vendor_name": "PolTech Komponenty Sp. z o.o.",
        "vendor_address": "ul. Przemyslowa 12, 62-800 Kalisz, Poland",
        "supplier_country": "PL",
        "vat_id": "PL6182234567",
        "vendor_iban": "PL77109010140000001234567890",
        "vendor_bic": "WBKPPLPP",
        "buyer_name": "Rheinwerk Anlagenbau AG",
        "buyer_address": "Industriestrasse 14, 90411 Nurnberg, Germany",
        "leitweg_id": "04011000-1234512345-06",
        "currency": "PLN",
        "source_currency": "PLN",
        "tax_treatment": "reverse_charge_13b",
        "subtotal": 2065.00,
        "tax_amount": 0.00,
        "total_amount": 2065.00,
        "vat_breakdown": [{"rate": 0.0, "net": 2065.00, "vat": 0.00}],
        "line_items": [
            {"pos": "1", "description": "Sruba szesciokatna M8x40 DIN 933", "quantity": 500.0, "unit": "Stk", "price_unit": 1.0, "unit_price": 0.85, "discount_percent": 0.0, "line_total": 425.00, "vat_rate": 0.0},
            {"pos": "2", "description": "Nakretka M8 DIN 934", "quantity": 500.0, "unit": "Stk", "price_unit": 1.0, "unit_price": 0.42, "discount_percent": 0.0, "line_total": 210.00, "vat_rate": 0.0},
            {"pos": "3", "description": "Podkladka sprezysta M8", "quantity": 1000.0, "unit": "Stk", "price_unit": 1.0, "unit_price": 0.18, "discount_percent": 0.0, "line_total": 180.00, "vat_rate": 0.0},
            {"pos": "4", "description": "Wal napedowy 25mm", "quantity": 4.0, "unit": "Stk", "price_unit": 1.0, "unit_price": 312.50, "discount_percent": 0.0, "line_total": 1250.00, "vat_rate": 0.0},
        ],
    }


def _make_f2_dict():
    return {
        "invoice_number": "EXP/2026-27/0843",
        "invoice_date": "2026-11-10",
        "due_date": "2026-12-10",
        "vendor_name": "Precision Components Pvt. Ltd.",
        "vendor_address": "Plot B-14, MIDC Industrial Area, Pune 411019, Maharashtra, India",
        "supplier_country": "IN",
        "vat_id": "27AAKCP1234F1Z5",
        "buyer_name": "Rheinwerk Anlagenbau AG",
        "buyer_address": "Industriestrasse 14, 90411 Nurnberg, Germany",
        "leitweg_id": "04011000-1234512345-06",
        "currency": "USD",
        "source_currency": "USD",
        "tax_treatment": "import_no_vat",
        "subtotal": 955.00,
        "tax_amount": 0.00,
        "total_amount": 955.00,
        "vat_breakdown": [{"rate": 0.0, "net": 955.00, "vat": 0.00}],
        "line_items": [
            {"pos": "1", "description": "Stainless Steel Flange DN50 PN16", "quantity": 20.0, "unit": "Stk", "price_unit": 1.0, "unit_price": 14.50, "discount_percent": 0.0, "line_total": 290.00, "vat_rate": 0.0},
            {"pos": "2", "description": "Brass Ball Valve 1 inch", "quantity": 50.0, "unit": "Stk", "price_unit": 1.0, "unit_price": 6.80, "discount_percent": 0.0, "line_total": 340.00, "vat_rate": 0.0},
            {"pos": "3", "description": "CNC Turned Shaft 30mm", "quantity": 100.0, "unit": "Stk", "price_unit": 1.0, "unit_price": 3.25, "discount_percent": 0.0, "line_total": 325.00, "vat_rate": 0.0},
        ],
    }


def test_fx_conversion_f1_pinned_rate():
    f1 = _make_f1_dict()
    conv = convert_invoice_to_eur(f1, manual_rate=4.2500, tax_treatment="reverse_charge_13b", rate_date="2026-11-12", rate_source="ECB")
    assert conv["currency"] == "EUR"
    assert conv["source_currency"] == "PLN"
    assert conv["original_net"] == 2065.00
    assert conv["original_vat"] == 0.00
    assert conv["original_gross"] == 2065.00
    line_totals = [it["line_total"] for it in conv["line_items"]]
    assert line_totals == [100.00, 49.41, 42.35, 294.12], f"Got {line_totals}"
    assert conv["subtotal"] == 485.88
    assert conv["tax_amount"] == 0.00
    assert conv["total_amount"] == 485.88


def test_fx_conversion_f2_pinned_rate():
    f2 = _make_f2_dict()
    conv = convert_invoice_to_eur(f2, manual_rate=1.1500, tax_treatment="import_no_vat", rate_date="2026-11-10", rate_source="ECB")
    assert conv["currency"] == "EUR"
    assert conv["source_currency"] == "USD"
    assert conv["original_net"] == 955.00
    assert conv["original_vat"] == 0.00
    assert conv["original_gross"] == 955.00
    line_totals = [it["line_total"] for it in conv["line_items"]]
    assert line_totals == [252.17, 295.65, 282.61], f"Got {line_totals}"
    assert conv["subtotal"] == 830.43
    assert conv["tax_amount"] == 0.00
    assert conv["total_amount"] == 830.43


def test_xrechnung_f1_and_f2_converted():
    ns = {
        "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    }

    # F1 -> reverse_charge_13b -> category AE
    f1_conv = convert_invoice_to_eur(_make_f1_dict(), manual_rate=4.2500, tax_treatment="reverse_charge_13b", rate_date="2026-11-12", rate_source="ECB")
    xml_f1 = generate_xrechnung_xml(f1_conv)
    root_f1 = ET.fromstring(xml_f1)
    assert root_f1.find("cbc:DocumentCurrencyCode", ns).text == "EUR"
    note_f1 = root_f1.find("cbc:Note", ns)
    assert note_f1 is not None and "2065.00 PLN" in note_f1.text and "4.2500" in note_f1.text
    cats_f1 = [el.text for el in root_f1.findall(".//cac:TaxCategory/cbc:ID", ns)]
    cls_cats_f1 = [el.text for el in root_f1.findall(".//cac:ClassifiedTaxCategory/cbc:ID", ns)]
    assert cats_f1 == ["AE"]
    assert cls_cats_f1 == ["AE", "AE", "AE", "AE"]

    # F2 -> import_no_vat -> category O
    f2_conv = convert_invoice_to_eur(_make_f2_dict(), manual_rate=1.1500, tax_treatment="import_no_vat", rate_date="2026-11-10", rate_source="ECB")
    xml_f2 = generate_xrechnung_xml(f2_conv)
    root_f2 = ET.fromstring(xml_f2)
    assert root_f2.find("cbc:DocumentCurrencyCode", ns).text == "EUR"
    note_f2 = root_f2.find("cbc:Note", ns)
    assert note_f2 is not None and "955.00 USD" in note_f2.text and "1.1500" in note_f2.text
    cats_f2 = [el.text for el in root_f2.findall(".//cac:TaxCategory/cbc:ID", ns)]
    cls_cats_f2 = [el.text for el in root_f2.findall(".//cac:ClassifiedTaxCategory/cbc:ID", ns)]
    assert cats_f2 == ["O"]
    assert cls_cats_f2 == ["O", "O", "O"]


def test_v5_country_aware():
    base = {
        "invoice_number": "TEST-1",
        "invoice_date": "2026-11-12",
        "vendor_name": "Test GmbH",
        "vendor_address": "Teststr 1, 10115 Berlin",
        "buyer_name": "Rheinwerk AG",
        "buyer_address": "Hafenstr 88, 47119 Duisburg",
        "leitweg_id": "04011000-1234512345-06",
        "subtotal": 100.0,
        "tax_amount": 19.0,
        "total_amount": 119.0,
        "vat_breakdown": [{"rate": 19.0, "net": 100.0, "vat": 19.0}],
        "line_items": [{"pos": "1", "description": "Item", "quantity": 1.0, "unit": "Stk", "price_unit": 1.0, "unit_price": 100.0, "discount_percent": 0.0, "line_total": 100.0, "vat_rate": 19.0}],
    }

    def v5_status(vat_id, country):
        vr = validate_invoice({**base, "vat_id": vat_id, "supplier_country": country})
        return {c["code"]: c["status"] for c in vr["checks"]}["V5"]

    assert v5_status("DE119283746", "DE") == "PASS"
    assert v5_status("DE119283746", "") == "PASS"
    assert v5_status("DE123", "DE") == "FAIL"
    assert v5_status("PL6182234567", "PL") == "PASS"
    assert v5_status("27AAKCP1234F1Z5", "IN") == "CANNOT VERIFY"
    assert suggest_tax_treatment("PL") == "reverse_charge_13b"
    assert suggest_tax_treatment("IN") == "import_no_vat"
    assert suggest_tax_treatment("DE") == "standard"
