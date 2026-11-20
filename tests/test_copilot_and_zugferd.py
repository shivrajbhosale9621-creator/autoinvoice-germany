import io
import os
import tempfile
from pypdf import PdfReader

import vector_store
from orchestrator import MainOrchestrator
from zugferd import generate_zugferd_pdf, check_zugferd_validation_gate
from xrechnung import generate_xrechnung_xml


def _make_f1():
    return {
        "doc_id": "test_f1",
        "invoice_number": "FV/2026/1147",
        "vendor_name": "PolTech Komponenty Sp. z o.o.",
        "vendor_address": "ul. Przemyslowa 18, 61-541 Poznan, Poland",
        "vat_id": "PL6182234567",
        "supplier_country": "PL",
        "invoice_date": "2026-11-12",
        "due_date": "2026-12-12",
        "source_currency": "PLN",
        "currency": "PLN",
        "subtotal": 2065.00,
        "tax_amount": 0.00,
        "total_amount": 2065.00,
        "original_net": 2065.00,
        "original_vat": 0.00,
        "original_gross": 2065.00,
        "tax_treatment": "reverse_charge_13b",
        "payment_status": "Unpaid",
        "line_items": [
            {"pos": "1", "description": "CNC Shaft 20mm", "quantity": 10, "unit": "Stk", "price_unit": 1, "unit_price": 42.50, "discount_percent": 0, "line_total": 425.00, "vat_rate": 0},
        ],
        "vat_breakdown": [{"rate": 0.0, "net": 425.00, "vat": 0.00}],
    }


def test_copilot_lang_and_mixed_currency():
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    orig_db = vector_store.CHAT_DB_PATH
    vector_store.CHAT_DB_PATH = tmp_db.name
    vector_store.init_db(tmp_db.name)
    try:
        f1 = _make_f1()
        f2 = {
            "doc_id": "test_f2",
            "invoice_number": "EXP/2026-27/0843",
            "vendor_name": "Precision Components Pvt. Ltd.",
            "vendor_address": "Plot 42 MIDC Bhosari, Pune 411026, India",
            "vat_id": "27AAKCP1234F1Z5",
            "supplier_country": "IN",
            "invoice_date": "2026-11-14",
            "due_date": "",
            "source_currency": "USD",
            "currency": "USD",
            "subtotal": 955.00,
            "tax_amount": 0.00,
            "total_amount": 955.00,
            "original_net": 955.00,
            "original_vat": 0.00,
            "original_gross": 955.00,
            "tax_treatment": "import_no_vat",
            "payment_status": "Unpaid",
            "line_items": [
                {"pos": "1", "description": "Bearing Housing", "quantity": 10, "unit": "Stk", "price_unit": 1, "unit_price": 29.00, "discount_percent": 0, "line_total": 290.00, "vat_rate": 0},
            ],
            "vat_breakdown": [{"rate": 0.0, "net": 290.00, "vat": 0.00}],
        }
        vector_store.save_invoice_to_vector_store(f1, file_path="INV-F1.pdf", user_id="u1")
        vector_store.save_invoice_to_vector_store(f2, file_path="INV-F2.pdf", user_id="u1")
        orch = MainOrchestrator(user_id="u1", conversation_id="pytest_conv")

        ans_en = orch.run("What is the total of all invoices?", lang="en")
        ans_de = orch.run("Wie hoch ist die Gesamtsumme aller Rechnungen?", lang="de")
        assert "Summary" in ans_en
        assert "Auswertung" in ans_de
        assert "955,00 USD" in ans_en and "2.065,00 PLN" in ans_en
        assert "invoice_aggregate" not in ans_en and "SQLite" not in ans_en and "FTS5" not in ans_en

        excl_en = orch.run("What is the total excluding the last 2 invoices?", lang="en")
        assert "0,00" in excl_en

        last5_en = orch.run("Show the last 5 invoices with days remaining until due.", lang="en")
        assert "Only 2 invoices exist." in last5_en
    finally:
        vector_store.CHAT_DB_PATH = orig_db
        os.unlink(tmp_db.name)


def test_zugferd_generation_and_gate():
    f1 = {
        **_make_f1(),
        "buyer_name": "Rheinwerk Anlagenbau AG",
        "buyer_address": "Hafenstraße 88, 47119 Duisburg",
        "buyer_vat_id": "DE119283746",
        "leitweg_id": "04011000-1234512345-06",
        "vendor_iban": "DE30612901200457331008",
        "currency": "EUR",
        "fx_rate": 4.2500,
        "fx_rate_date": "2026-11-12",
        "fx_rate_source": "ECB",
        "subtotal": 425.00,
        "tax_amount": 0.00,
        "total_amount": 425.00,
    }
    gate_fail = check_zugferd_validation_gate({**f1, "leitweg_id": ""})
    assert not gate_fail["allowed"]

    pdf_bytes, fname, _ = generate_zugferd_pdf(f1)
    assert fname == "FV_2026_1147_ZUGFeRD.pdf"
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert reader.attachments["factur-x.xml"][0] == generate_xrechnung_xml(f1).encode("utf-8")
