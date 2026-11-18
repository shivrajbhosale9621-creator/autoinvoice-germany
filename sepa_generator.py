"""
sepa_generator.py - SEPA Credit Transfer pain.001.001.03 Generator & IBAN Validator (Task 10).

Implements:
- ISO 13616 Mod-97 IBAN checksum validation in Python.
- Enforces valid Debtor IBAN from Settings before generating any SEPA file.
- One bad creditor IBAN blocks only that payment, never the whole run, and is reported to the user.
- Generates German DK standard pain.001.001.03 XML file with Group Header (MsgId, CreDtTm,
  NbOfTxs, CtrlSum), Debtor block from Settings, and one CdtTrfTxInf per vendor payment.
- Records audit trail in SQLite `payment_runs` and marks paid invoices as 'Paid'.
"""

import re
import uuid
import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
from lxml import etree

from config import CHAT_DB_PATH
from chat_store import init_db, get_user_settings
from vector_store import get_invoice_by_identifier, parse_numeric, _user_id_variants

IBAN_COUNTRY_LENGTHS = {
    "DE": 22,
    "AT": 20,
    "CH": 21,
    "NL": 18,
    "FR": 27,
    "BE": 16,
    "IT": 27,
    "ES": 24,
    "LU": 20,
    "PL": 28,
}


def validate_iban_checksum(iban: str) -> Tuple[bool, str, str]:
    """
    Validate an IBAN according to ISO 13616 (length check + Modulo 97 checksum).
    Returns (is_valid, cleaned_iban, reason_message).
    """
    clean = re.sub(r"\s+", "", str(iban or "")).upper()
    if not clean:
        return False, "", "IBAN fehlt (leer)."

    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$", clean):
        return False, clean, f"IBAN '{iban}' hat ein ungueltiges Grundformat."

    cc = clean[:2]
    expected_len = IBAN_COUNTRY_LENGTHS.get(cc)
    if expected_len and len(clean) != expected_len:
        return (
            False,
            clean,
            f"IBAN '{clean}' hat Laenge {len(clean)} (fuer {cc} sind {expected_len} Zeichen vorgeschrieben).",
        )

    # Move first 4 chars to the end and convert A-Z -> 10-35
    rearranged = clean[4:] + clean[:4]
    numeric_chars = []
    for ch in rearranged:
        if ch.isdigit():
            numeric_chars.append(ch)
        else:
            numeric_chars.append(str(ord(ch) - ord("A") + 10))
    numeric_int = int("".join(numeric_chars))

    if numeric_int % 97 != 1:
        return False, clean, f"IBAN-Pruefziffer (Modulo 97) fuer '{clean}' ist ungueltig."

    return True, clean, "IBAN gueltig (Mod-97 OK)."


def preview_payment_run(
    invoice_identifiers: List[str],
    user_id: str,
    apply_skonto: bool = False,
    db_path: str = CHAT_DB_PATH,
) -> Dict[str, Any]:
    """
    Preview a SEPA payment run before human confirmation.
    Validates the debtor IBAN from Settings and every invoice's creditor IBAN,
    and computes exact EUR amounts.
    """
    init_db(db_path)
    settings_data = get_user_settings(user_id, db_path)
    raw_debtor_iban = str(settings_data.get("iban") or "").strip()
    debtor_iban_ok, debtor_iban_clean, debtor_iban_msg = validate_iban_checksum(raw_debtor_iban)

    valid_payments = []
    blocked_payments = []
    control_sum = 0.0

    for ident in invoice_identifiers:
        inv = get_invoice_by_identifier(ident, user_id=user_id)
        if not inv:
            blocked_payments.append({
                "invoice_number": ident,
                "vendor_name": "Unbekannt",
                "iban": "",
                "amount": 0.0,
                "reason": "Beleg nicht gefunden.",
            })
            continue

        inv_num = inv.get("invoice_number", ident)
        vendor_name = inv.get("vendor_name", "Unbekannt")
        raw_iban = inv.get("vendor_iban", "")
        raw_bic = inv.get("vendor_bic", "GENODES1NUE")
        gross = parse_numeric(inv.get("total_amount"), 0.0)
        skonto_amt = parse_numeric(inv.get("skonto_amount"), 0.0)

        if inv.get("document_type") == "credit_note" or gross <= 0:
            blocked_payments.append({
                "doc_id": inv.get("doc_id"),
                "invoice_number": inv_num,
                "vendor_name": vendor_name,
                "iban": raw_iban,
                "amount": gross,
                "reason": "Gutschrift oder Betrag <= 0,00 EUR (keine Ueberweisung).",
            })
            continue

        iban_ok, iban_clean, iban_reason = validate_iban_checksum(raw_iban)
        pay_amount = round(gross - skonto_amt, 2) if (apply_skonto and skonto_amt > 0) else round(gross, 2)

        if not iban_ok:
            blocked_payments.append({
                "doc_id": inv.get("doc_id"),
                "invoice_number": inv_num,
                "vendor_name": vendor_name,
                "iban": raw_iban,
                "amount": pay_amount,
                "reason": iban_reason,
            })
            continue

        control_sum = round(control_sum + pay_amount, 2)
        valid_payments.append({
            "id": inv.get("id"),
            "doc_id": inv.get("doc_id"),
            "invoice_number": inv_num,
            "vendor_name": vendor_name,
            "iban": iban_clean,
            "iban_display": raw_iban,
            "bic": str(raw_bic or "NOTPROVIDED").strip(),
            "gross_amount": gross,
            "skonto_applied": round(skonto_amt, 2) if (apply_skonto and skonto_amt > 0) else 0.0,
            "amount": pay_amount,
            "remittance_info": f"Rechnung {inv_num}",
            "iban_change_flag": inv.get("iban_change_flag", 0),
        })

    return {
        "debtor": {
            "name": settings_data.get("company_name") or "Rheinwerk Anlagenbau AG",
            "iban": debtor_iban_clean or re.sub(r"\s+", "", raw_debtor_iban),
            "iban_valid": debtor_iban_ok,
            "iban_message": debtor_iban_msg,
            "bic": settings_data.get("bic") or "COBADEFFXXX",
        },
        "valid_payments": valid_payments,
        "blocked_payments": blocked_payments,
        "tx_count": len(valid_payments),
        "control_sum": round(control_sum, 2),
        "currency": "EUR",
    }


def execute_approved_payment_run(
    invoice_identifiers: List[str],
    user_id: str,
    approved_by: str = "Human Bookkeeper",
    apply_skonto: bool = False,
    execution_date: str = "",
    db_path: str = CHAT_DB_PATH,
) -> Dict[str, Any]:
    """
    Generate the SEPA pain.001.001.03 XML file after explicit human approval,
    persist the audit record in `payment_runs`, and mark paid invoices as 'Paid'.
    Refuses generation if the debtor IBAN in Settings fails Mod-97 validation.
    """
    preview = preview_payment_run(
        invoice_identifiers=invoice_identifiers,
        user_id=user_id,
        apply_skonto=apply_skonto,
        db_path=db_path,
    )
    valid_txs = preview["valid_payments"]
    blocked_txs = preview["blocked_payments"]
    debtor = preview["debtor"]

    if not debtor.get("iban_valid"):
        bad_debtor_iban = debtor.get("iban") or "(leer)"
        raise ValueError(
            f"SEPA-Zahllauf abgelehnt: Ungueltige Debitor-IBAN '{bad_debtor_iban}' in den Einstellungen ({debtor.get('iban_message')})."
        )

    if not valid_txs:
        raise ValueError(
            "Keine gueltigen Zahlungen im Zahllauf (alle ausgewaehlten Belege wurden wegen ungueltiger IBAN oder Betrag <= 0 blockiert)."
        )

    now_utc = datetime.now(timezone.utc)
    cre_dt_tm = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    req_exec_dt = execution_date if re.match(r"^\d{4}-\d{2}-\d{2}$", str(execution_date or "")) else now_utc.strftime("%Y-%m-%d")

    run_uid = uuid.uuid4().hex[:10].upper()
    msg_id = f"MSG-{now_utc.strftime('%Y%m%d%H%M%S')}-{run_uid[:6]}"
    pmt_inf_id = f"PMT-{now_utc.strftime('%Y%m%d')}-{run_uid[:6]}"
    run_id = f"sepa_{now_utc.strftime('%Y%m%d_%H%M%S')}_{run_uid[:6].lower()}"
    file_name = f"{run_id}_pain001.xml"

    tx_count = len(valid_txs)
    ctrl_sum_str = f"{preview['control_sum']:.2f}"

    NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"
    XSI = "http://www.w3.org/2001/XMLSchema-instance"
    root = etree.Element(
        "Document",
        nsmap={None: NS, "xsi": XSI},
        attrib={f"{{{XSI}}}schemaLocation": f"{NS} pain.001.001.03.xsd"},
    )
    cstmr = etree.SubElement(root, "CstmrCdtTrfInitn")

    # 1. Group Header (GrpHdr)
    grp_hdr = etree.SubElement(cstmr, "GrpHdr")
    etree.SubElement(grp_hdr, "MsgId").text = msg_id
    etree.SubElement(grp_hdr, "CreDtTm").text = cre_dt_tm
    etree.SubElement(grp_hdr, "NbOfTxs").text = str(tx_count)
    etree.SubElement(grp_hdr, "CtrlSum").text = ctrl_sum_str
    initg_pty = etree.SubElement(grp_hdr, "InitgPty")
    etree.SubElement(initg_pty, "Nm").text = debtor["name"][:70]

    # 2. Payment Information (PmtInf) & Debtor Block
    pmt_inf = etree.SubElement(cstmr, "PmtInf")
    etree.SubElement(pmt_inf, "PmtInfId").text = pmt_inf_id
    etree.SubElement(pmt_inf, "PmtMtd").text = "TRF"
    etree.SubElement(pmt_inf, "BtchBookg").text = "true"
    etree.SubElement(pmt_inf, "NbOfTxs").text = str(tx_count)
    etree.SubElement(pmt_inf, "CtrlSum").text = ctrl_sum_str

    pmt_tp = etree.SubElement(pmt_inf, "PmtTpInf")
    svc_lvl = etree.SubElement(pmt_tp, "SvcLvl")
    etree.SubElement(svc_lvl, "Cd").text = "SEPA"

    etree.SubElement(pmt_inf, "ReqdExctnDt").text = req_exec_dt

    dbtr = etree.SubElement(pmt_inf, "Dbtr")
    etree.SubElement(dbtr, "Nm").text = debtor["name"][:70]

    dbtr_acct = etree.SubElement(pmt_inf, "DbtrAcct")
    dbtr_acct_id = etree.SubElement(dbtr_acct, "Id")
    etree.SubElement(dbtr_acct_id, "IBAN").text = debtor["iban"]

    dbtr_agt = etree.SubElement(pmt_inf, "DbtrAgt")
    dbtr_fin = etree.SubElement(dbtr_agt, "FinInstnId")
    etree.SubElement(dbtr_fin, "BIC").text = debtor["bic"]

    etree.SubElement(pmt_inf, "ChrgBr").text = "SLEV"

    # 3. Credit Transfer Transaction Information (CdtTrfTxInf) per vendor invoice
    for idx, tx in enumerate(valid_txs, start=1):
        tx_el = etree.SubElement(pmt_inf, "CdtTrfTxInf")
        pmt_id = etree.SubElement(tx_el, "PmtId")
        e2e = re.sub(r"[^A-Za-z0-9/\-?:().,'+ ]", "", f"E2E-{tx['invoice_number']}-{idx}")[:35]
        etree.SubElement(pmt_id, "EndToEndId").text = e2e

        amt_el = etree.SubElement(tx_el, "Amt")
        etree.SubElement(amt_el, "InstdAmt", Ccy="EUR").text = f"{tx['amount']:.2f}"

        cdtr_agt = etree.SubElement(tx_el, "CdtrAgt")
        cdtr_fin = etree.SubElement(cdtr_agt, "FinInstnId")
        etree.SubElement(cdtr_fin, "BIC").text = tx["bic"] or "NOTPROVIDED"

        cdtr = etree.SubElement(tx_el, "Cdtr")
        etree.SubElement(cdtr, "Nm").text = tx["vendor_name"][:70]

        cdtr_acct = etree.SubElement(tx_el, "CdtrAcct")
        cdtr_acct_id = etree.SubElement(cdtr_acct, "Id")
        etree.SubElement(cdtr_acct_id, "IBAN").text = tx["iban"]

        rmt_inf = etree.SubElement(tx_el, "RmtInf")
        etree.SubElement(rmt_inf, "Ustrd").text = tx["remittance_info"][:140]

    xml_str = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    ).decode("utf-8")

    # 4. Record audit trail in SQLite and mark paid invoices as Paid
    u_clean = str(user_id).replace("-", "")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        conn.execute(
            """
            INSERT INTO payment_runs (
                run_id, user_id, message_id, created_at, execution_date,
                debtor_name, debtor_iban, debtor_bic, tx_count, control_sum,
                file_name, xml_content, paid_invoices_json, blocked_invoices_json, approved_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                u_clean,
                msg_id,
                cre_dt_tm,
                req_exec_dt,
                debtor["name"],
                debtor["iban"],
                debtor["bic"],
                tx_count,
                preview["control_sum"],
                file_name,
                xml_str,
                json.dumps(valid_txs, ensure_ascii=False),
                json.dumps(blocked_txs, ensure_ascii=False),
                approved_by,
            ),
        )
        for tx in valid_txs:
            conn.execute(
                "UPDATE invoices SET payment_status = 'Paid', payment_run_id = ? WHERE id = ?",
                (run_id, tx["id"]),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "run_id": run_id,
        "message_id": msg_id,
        "created_at": cre_dt_tm,
        "execution_date": req_exec_dt,
        "file_name": file_name,
        "tx_count": tx_count,
        "control_sum": preview["control_sum"],
        "paid_invoices": valid_txs,
        "blocked_invoices": blocked_txs,
        "approved_by": approved_by,
        "xml_content": xml_str,
    }


def list_payment_runs(user_id: str, db_path: str = CHAT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    u_variants = _user_id_variants(user_id)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        if u_variants:
            placeholders = ",".join("?" for _ in u_variants)
            rows = conn.execute(
                f"SELECT * FROM payment_runs WHERE user_id IN ({placeholders}) ORDER BY created_at DESC",
                u_variants,
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM payment_runs ORDER BY created_at DESC").fetchall()

        out = []
        for r in rows:
            d = dict(r)
            try:
                d["paid_invoices"] = json.loads(d.get("paid_invoices_json") or "[]")
            except Exception:
                d["paid_invoices"] = []
            try:
                d["blocked_invoices"] = json.loads(d.get("blocked_invoices_json") or "[]")
            except Exception:
                d["blocked_invoices"] = []
            d.pop("xml_content", None)
            out.append(d)
        return out
    finally:
        conn.close()


def get_payment_run_xml(run_id: str, user_id: str, db_path: str = CHAT_DB_PATH) -> Tuple[str, str]:
    init_db(db_path)
    u_variants = _user_id_variants(user_id)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        if u_variants:
            placeholders = ",".join("?" for _ in u_variants)
            row = conn.execute(
                f"SELECT file_name, xml_content FROM payment_runs WHERE run_id = ? AND user_id IN ({placeholders})",
                [run_id, *u_variants],
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT file_name, xml_content FROM payment_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return "", ""
        return str(row["file_name"]), str(row["xml_content"])
    finally:
        conn.close()
