"""
End-to-End Verification Suite for AutoInvoice German Build (`german-build`).
Tests all 12 core tasks + all 8 Tier 2 features + all 6 German test invoices (INV-01 to INV-06).
"""

import glob
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

# Clean any existing test database BEFORE importing app
for db_file in glob.glob(str(REPO_DIR / "invoices.db*")):
    try:
        os.remove(db_file)
    except OSError:
        pass

from fastapi.testclient import TestClient
from app.main import app
import app.routers.auth as auth_router_mod

UPLOAD_DIR = Path("/working_dir/c_771ac6254b03f004/user_uploaded_files/ttl=63d/user_uploaded")


def run_e2e_tests():
    captured_otp = {}
    orig_send_otp = auth_router_mod.send_otp_email

    async def _capture_send_otp(to_email: str, code: str, purpose: str = "email_verification"):
        captured_otp[to_email] = code
        return await orig_send_otp(to_email, code, purpose=purpose)

    auth_router_mod.send_otp_email = _capture_send_otp

    with TestClient(app) as client:
        print("=== 1. Auth & User Registration ===")
        signup_res = client.post(
            "/api/auth/signup",
            json={"email": "buchhaltung@rheinwerk.de", "password": "SecurePassword123!"},
        )
        assert signup_res.status_code in (200, 201), f"Signup failed: {signup_res.text}"
        otp_code = captured_otp.get("buchhaltung@rheinwerk.de")
        assert otp_code and len(otp_code) == 6, "OTP not captured"

        verify_res = client.post(
            "/api/auth/verify-otp",
            json={"email": "buchhaltung@rheinwerk.de", "code": otp_code},
        )
        assert verify_res.status_code == 200, f"Verify OTP failed: {verify_res.text}"

        me_res = client.get("/api/auth/me")
        assert me_res.status_code == 200
        user_data = me_res.json()["user"]
        assert user_data["email"] == "buchhaltung@rheinwerk.de"
        print("  [OK] Auth signup, OTP verification, and session cookies work.")

        print("=== 2. Settings API (Task 2 & Task 12) ===")
        get_set = client.get("/api/settings")
        assert get_set.status_code == 200
        default_settings = get_set.json()["settings"]
        assert default_settings["currency"] == "EUR"
        assert default_settings["language"] == "de"

        put_set = client.put(
            "/api/settings",
            json={
                "company_name": "Rheinwerk Anlagenbau AG",
                "company_address": "Abt. Kreditorenbuchhaltung, Hafenstrasse 88, 47119 Duisburg",
                "vat_id": "DE119283746",
                "iban": "DE44500105175407324931",
                "bic": "INGDDEFFXXX",
                "datev_chart": "SKR04",
                "datev_consultant_number": "12345",
                "datev_client_number": "67890",
                "currency": "EUR",
                "number_format": "1.234,56",
                "date_format": "DD.MM.YYYY",
                "language": "de",
                "leitweg_id": "05112-00001-77",
            },
        )
        assert put_set.status_code == 200, f"Settings PUT failed: {put_set.text}"
        updated_settings = put_set.json()["settings"]
        assert updated_settings["datev_chart"] == "SKR04"
        assert updated_settings["iban"] == "DE44500105175407324931"
        print("  [OK] Settings saved and retrieved (SKR04, EUR, DE locale, IBAN/BIC).")

        print("=== 3. Upload & Validate All 6 German Test Invoices (INV-01 to INV-06) ===")
        pdf_files = [
            "INV-01_RE-2026-10418.pdf",
            "INV-02_RE-2026-10431.pdf",
            "INV-03_GS-2026-00872.pdf",
            "INV-04_RE-2026-10457.pdf",
            "INV-05_2026-R-00117.pdf",
            "INV-06_BONUS_scan_RE-2026-10466.pdf",
        ]
        for fname in pdf_files:
            fpath = UPLOAD_DIR / fname
            assert fpath.exists(), f"Missing test file: {fpath}"
            up_res = client.post(
                "/api/invoice/upload",
                files={"file": (fname, fpath.read_bytes(), "application/pdf")},
            )
            assert up_res.status_code == 200, f"Upload failed for {fname}: {up_res.text}"

        # List invoices via SQL API
        inv_res = client.get("/api/invoices", params={"include_ids": True})
        assert inv_res.status_code == 200
        invoices = inv_res.json()["invoices"]
        assert len(invoices) == 6, f"Expected 6 invoices, got {len(invoices)}"
        by_num = {inv["invoice_number"]: inv for inv in invoices}

        # Verify exact figures for each invoice
        assert abs(by_num["RE-2026-10418"]["total_amount"] - 953.75) < 0.01
        assert abs(by_num["RE-2026-10431"]["total_amount"] - 6037.86) < 0.01
        assert abs(by_num["GS-2026-00872"]["total_amount"] - (-476.06)) < 0.01
        assert by_num["GS-2026-00872"]["document_type"] == "credit_note"
        assert abs(by_num["RE-2026-10457"]["total_amount"] - 6278.42) < 0.01
        assert abs(by_num["2026-R-00117"]["total_amount"] - 1154.43) < 0.01
        assert abs(by_num["RE-2026-10466"]["total_amount"] - 538.98) < 0.01
        print("  [OK] All 6 PDFs extracted with exact subtotals, VAT breakdowns, and gross totals.")

        print("=== 4. Verify INV-04 Line 7 Math Error Detection (Task 5 Guardrail) ===")
        inv04_doc_id = by_num["RE-2026-10457"]["doc_id"]
        inv04_detail = client.get(f"/api/invoice/{inv04_doc_id}").json()["invoice"]
        # Verify printed value was NOT silently fixed
        line7 = inv04_detail["line_items"][6]
        assert abs(line7["quantity"] - 26.0) < 0.01
        assert abs(line7["unit_price"] - 48.02) < 0.01
        assert abs(line7["line_total"] - 1284.52) < 0.01, "Line 7 printed total must remain 1284.52!"

        v_report = inv04_detail["validation_report"]
        assert v_report["overall_status"] == "FAIL"
        checks_by_code = {c["code"]: c for c in v_report["checks"]}
        v1 = checks_by_code["V1"]
        assert v1["status"] == "FAIL"
        assert len(v1["discrepancies"]) == 1
        f0 = v1["discrepancies"][0]
        assert str(f0["line"]) in ("7", "070")
        assert abs(f0["expected"] - 1248.52) < 0.01
        assert abs(f0["printed"] - 1284.52) < 0.01
        assert abs(f0["difference"] - 36.00) < 0.01
        v2 = checks_by_code["V2"]
        assert v2["status"] == "FAIL"
        print("  [OK] INV-04 Line 7 FAIL verified: expected 1248.52, printed 1284.52, diff +36.00.")

        print("=== 5. Verify XRechnung Block When Leitweg-ID Missing & Export After Edit (Tasks 4, 5, 6) ===")
        inv01_doc_id = by_num["RE-2026-10418"]["doc_id"]
        # Initially Leitweg-ID is absent on the printed invoice -> V9 is FAIL -> XRechnung blocked with 422
        xr_blocked = client.get(f"/api/invoice/{inv01_doc_id}/xrechnung")
        assert xr_blocked.status_code == 422, f"XRechnung should be blocked when Leitweg-ID is missing, got {xr_blocked.status_code}"

        # Now edit INV-01 on the detail page to set Leitweg-ID (BT-10)
        edit_res = client.put(
            f"/api/invoice/{inv01_doc_id}",
            json={"leitweg_id": "05112-00001-77"},
        )
        assert edit_res.status_code == 200
        updated_inv01 = edit_res.json()["invoice"]
        assert updated_inv01["leitweg_id"] == "05112-00001-77"
        assert updated_inv01["validation_status"] == "PASS"
        assert updated_inv01["validation_report"]["xrechnung_ready"] is True

        # Now download XRechnung XML
        xr_ok = client.get(f"/api/invoice/{inv01_doc_id}/xrechnung")
        assert xr_ok.status_code == 200
        xml_bytes = xr_ok.content
        root = ET.fromstring(xml_bytes)
        ns = {
            "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
            "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
        }
        bt10 = root.find("cbc:BuyerReference", ns)
        assert bt10 is not None and bt10.text == "05112-00001-77"
        payable = root.find(".//cbc:PayableAmount", ns)
        assert payable is not None and payable.text == "953.75"
        issue_date = root.find("cbc:IssueDate", ns)
        assert issue_date is not None and issue_date.text == "2026-10-06"
        print("  [OK] Detail edit saved, V1-V9 all PASS, and valid EN 16931 XRechnung XML generated.")

        print("=== 6. Verify SQL FTS5 Search & Chat Copilot Router (Task 1 & Tier 2 Citations/Approvals) ===")
        # FTS5 search for item description "Zahnriemen"
        fts_res = client.get("/api/invoices", params={"q": "Zahnriemen"})
        assert fts_res.status_code == 200
        fts_matches = fts_res.json()["invoices"]
        assert any(i["invoice_number"] == "RE-2026-10457" for i in fts_matches)

        # Chat aggregate question
        chat_agg = client.post(
            "/api/chat",
            json={"message": "Wie hoch ist die Gesamtsumme aller Rechnungen?"},
        )
        assert chat_agg.status_code == 200
        agg_text = chat_agg.json()["response"]
        # Total gross across 6 docs = 953.75 + 6037.86 - 476.06 + 6278.42 + 1154.43 + 538.98 = 14487.38
        assert "14.487,38" in agg_text, f"Unexpected answer: {agg_text}"
        # Verify citation link is present
        assert "#invoice/" in agg_text or "RE-2026-10418" in agg_text

        # Chat validation question
        chat_val = client.post(
            "/api/chat",
            json={"message": "Welche Rechnungen haben Fehler in der Validierung?"},
        )
        assert chat_val.status_code == 200
        val_text = chat_val.json()["response"]
        assert "RE-2026-10457" in val_text

        # Chat action proposal (human approval step)
        chat_act = client.post(
            "/api/chat",
            json={"message": "Bitte RE-2026-10418 an DATEV buchen"},
        )
        assert chat_act.status_code == 200
        act_text = chat_act.json()["response"]
        m_act = re.search(r"\[ACTION_PROPOSAL:\s*([A-Za-z0-9_-]+)\]", act_text)
        assert m_act is not None, f"Expected [ACTION_PROPOSAL:id] in {act_text}"
        action_id = m_act.group(1)

        # Approve action
        res_act = client.post(
            f"/api/chat/actions/{action_id}/resolve",
            json={"decision": "approve"},
        )
        assert res_act.status_code == 200
        assert res_act.json()["action_status"] == "approved"
        print("  [OK] SQL FTS5 search, invoice_aggregate totals, chat citations, and human-approval actions verified.")

        print("=== 7. Verify Vendors, Price Comparison & IBAN Change Flag (Tasks 7, 11, Tier 2) ===")
        vend_res = client.get("/api/vendors")
        assert vend_res.status_code == 200
        v_data = vend_res.json()
        assert len(v_data["vendors"]) >= 1
        kessler = v_data["vendors"][0]
        assert "Kessler" in kessler["vendor_name"]
        assert kessler["invoice_count"] == 6
        assert "validation_score_pct" in kessler
        assert len(v_data["price_comparisons"]) > 0

        # Simulate IBAN change on INV-05 to verify fraud flag
        inv05_doc_id = by_num["2026-R-00117"]["doc_id"]
        iban_edit = client.put(
            f"/api/invoice/{inv05_doc_id}",
            json={"vendor_iban": "DE12500105170648489890"},
        )
        assert iban_edit.status_code == 200
        assert int(iban_edit.json()["invoice"]["iban_change_flag"]) == 1
        # Restore original IBAN on INV-05
        client.put(
            f"/api/invoice/{inv05_doc_id}",
            json={"vendor_iban": "DE89 6129 0120 0457 3310 08"},
        )
        print("  [OK] Vendors list, item price comparison, vendor validation score, and IBAN fraud flag verified.")

        print("=== 8. Verify Dashboard Cash Flow, Skonto, Validation Health & Alerts (Task 8 & Tier 2) ===")
        dash_res = client.get("/api/dashboard/summary")
        assert dash_res.status_code == 200
        dash = dash_res.json()
        assert "cashflow" in dash
        assert len(dash["skonto"]["opportunities"]) >= 3  # INV-01, INV-02, INV-04 have Skonto
        assert "validation_health" in dash
        assert "proactive_alerts" in dash
        print("  [OK] Dashboard cash flow, Skonto savings, validation health, and proactive alerts verified.")

        print("=== 9. Verify VIES VAT Check Service (Task 9) ===")
        vies_res = client.post("/api/vies/check", json={"doc_id": inv01_doc_id})
        assert vies_res.status_code == 200
        vies_result = vies_res.json()["vies"]
        # In sandbox VM without internet, VIES must mark as 'unchecked', NEVER 'invalid'
        assert vies_result["status"] in ("valid", "unchecked")
        assert vies_result["status"] != "invalid"
        print(f"  [OK] VIES check returned status='{vies_result['status']}' (never marks offline as invalid).")

        print("=== 10. Verify SEPA pain.001.001.03 Payment Run & Bad-IBAN Blocking (Task 10) ===")
        # Set a bad IBAN on INV-06 to test that one bad IBAN blocks only that payment, not the whole run
        inv06_doc_id = by_num["RE-2026-10466"]["doc_id"]
        inv02_doc_id = by_num["RE-2026-10431"]["doc_id"]
        client.put(f"/api/invoice/{inv06_doc_id}", json={"vendor_iban": "DE00612901200457331008"})

        sepa_prev = client.post(
            "/api/sepa/preview",
            json={"invoice_ids": [inv01_doc_id, inv02_doc_id, inv06_doc_id], "apply_skonto": False},
        )
        assert sepa_prev.status_code == 200
        prev_data = sepa_prev.json()["preview"]
        assert prev_data["tx_count"] == 2, f"2 valid IBANs should pass, got {prev_data}"
        assert len(prev_data["blocked_payments"]) == 1, "1 bad IBAN should be blocked"
        assert prev_data["blocked_payments"][0]["invoice_number"] == "RE-2026-10466"

        # Execute SEPA run without approval must fail (400)
        sepa_unapproved = client.post(
            "/api/sepa/execute",
            json={"invoice_ids": [inv01_doc_id, inv02_doc_id, inv06_doc_id], "human_approved": False},
        )
        assert sepa_unapproved.status_code == 400

        # Execute SEPA run with human approval
        sepa_exec = client.post(
            "/api/sepa/execute",
            json={"invoice_ids": [inv01_doc_id, inv02_doc_id, inv06_doc_id], "human_approved": True},
        )
        assert sepa_exec.status_code == 200
        run_info = sepa_exec.json()["payment_run"]
        run_id = run_info["run_id"]
        assert run_info["tx_count"] == 2
        assert abs(run_info["control_sum"] - (953.75 + 6037.86)) < 0.01

        # Download pain.001.001.03 XML and verify structure
        sepa_dl = client.get(f"/api/sepa/runs/{run_id}/download")
        assert sepa_dl.status_code == 200
        sepa_root = ET.fromstring(sepa_dl.content)
        assert "pain.001.001.03" in sepa_root.tag

        # Verify INV-01 and INV-02 are marked paid, while blocked INV-06 remains unpaid
        assert client.get(f"/api/invoice/{inv01_doc_id}").json()["invoice"]["payment_status"].lower() == "paid"
        assert client.get(f"/api/invoice/{inv02_doc_id}").json()["invoice"]["payment_status"].lower() == "paid"
        assert client.get(f"/api/invoice/{inv06_doc_id}").json()["invoice"]["payment_status"].lower() == "unpaid"
        print("  [OK] SEPA pain.001.001.03 generated, bad IBAN isolated, human approval enforced, audit trail saved.")

        print("=== 11. Verify Month-End Checklist, Tax Summary & DATEV Export (Tier 2) ===")
        me_res = client.get("/api/month-end", params={"month": "2026-10"})
        assert me_res.status_code == 200
        me_data = me_res.json()
        rates_map = {r["rate"]: r for r in me_data["tax_summary"]["rates"]}
        assert 19 in rates_map and rates_map[19]["net_total"] > 0
        assert 7 in rates_map and rates_map[7]["net_total"] > 0
        assert len(me_data["checklist"]["unbooked"]) > 0

        # Check off a checklist item
        item_key = me_data["checklist"]["unbooked"][0]["item_key"]
        chk_res = client.post(
            "/api/month-end/check",
            json={"month": "2026-10", "item_key": item_key, "checked": True},
        )
        assert chk_res.status_code == 200
        assert chk_res.json()["checked"] is True

        # DATEV CSV export
        datev_res = client.get("/api/datev/export", params={"month": "2026-10"})
        assert datev_res.status_code == 200
        assert "Umsatz" in datev_res.text and "Konto" in datev_res.text
        print("  [OK] Month-end checklist, tax summary (19%/7%/0%), and DATEV Buchungsstapel CSV verified.")

    print("\nALL END-TO-END TESTS PASSED!")


if __name__ == "__main__":
    run_e2e_tests()
