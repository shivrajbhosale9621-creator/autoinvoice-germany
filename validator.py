"""
validator.py - 9-Rule German Invoice Validation Engine in Python (Task 5).

All 9 validation checks run deterministically in Python, NEVER in the LLM:
  V1: quantity x unit_price / price_unit x (1 - discount) = line_total (tolerance 0.01)
  V2: per VAT rate, sum of line totals = net for that rate (tolerance 0.01)
  V3: per VAT rate, net x rate = VAT for that rate (tolerance 0.01)
  V4: net total + VAT total = gross total (tolerance 0.01)
  V5: supplier VAT ID matches DE plus 9 digits (and VIES not invalid)
  V6: dates are valid and due date is not before document date
  V7: credit notes have all negative amounts, invoices all positive
  V8: mandatory fields present per section 14 of the German VAT Act (§ 14 UStG)
  V9: Leitweg-ID present (mandatory for XRechnung; if missing, invoice cannot be sent as XRechnung)

Rules:
- Extract what is printed, never silently fix a wrong number.
- Every FAIL states which line/rate/field, the expected value, the printed value, and the difference.
- When a check cannot run because data was not extracted, report CANNOT VERIFY, never FAIL.
"""

import re
from datetime import datetime
from typing import Dict, Any, List, Optional

from vector_store import parse_numeric, parse_german_date, format_number_de, format_eur_de

TOLERANCE = 0.01


def _parse_iso_date_obj(val: Any) -> Optional[datetime]:
    iso = parse_german_date(val)
    if not iso or not re.match(r"^\d{4}-\d{2}-\d{2}$", iso):
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%d")
    except ValueError:
        return None


def validate_invoice(inv: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run all 9 German validation checks on a normalized invoice dictionary.
    Also annotates each item in `inv['line_items']` with `validation_status`,
    `expected_total`, `difference`, and `validation_message` for the detail page.
    """
    line_items: List[Dict[str, Any]] = inv.get("line_items") or inv.get("items") or []
    vat_breakdown: List[Dict[str, Any]] = inv.get("vat_breakdown") or []
    doc_type = str(inv.get("document_type") or "invoice").lower()
    is_credit_note = ("credit" in doc_type or "gutschrift" in doc_type or str(inv.get("invoice_number") or "").upper().startswith("GS-"))

    subtotal = parse_numeric(inv.get("subtotal"), 0.0)
    tax_amount = parse_numeric(inv.get("tax_amount") if inv.get("tax_amount") is not None else inv.get("tax"), 0.0)
    total_amount = parse_numeric(inv.get("total_amount"), 0.0)

    checks: List[Dict[str, Any]] = []
    line_flags: List[Dict[str, Any]] = []

    # ─────────────────────────────────────────────────────────────────────────
    # V1: quantity x unit_price / price_unit x (1 - discount) = line_total
    # ─────────────────────────────────────────────────────────────────────────
    if not line_items:
        checks.append({
            "code": "V1",
            "name": "Positionsberechnung (Menge x Einzelpreis / PE x (1 - Rabatt))",
            "name_en": "Line Item Math (qty x unit_price / price_unit x (1 - discount))",
            "status": "CANNOT VERIFY",
            "message": "Keine Rechnungspositionen extrahiert; Positionsmathematik kann nicht geprüft werden.",
            "discrepancies": [],
        })
    else:
        v1_discrepancies = []
        v1_unverifiable = False
        for idx, it in enumerate(line_items, start=1):
            pos_label = str(it.get("pos") or idx)
            qty = it.get("quantity") if it.get("quantity") is not None else it.get("qty")
            unit_price = it.get("unit_price") if it.get("unit_price") is not None else it.get("rate")
            line_total = it.get("line_total") if it.get("line_total") is not None else it.get("total")
            if qty is None or unit_price is None or line_total is None:
                v1_unverifiable = True
                it["validation_status"] = "CANNOT VERIFY"
                it["validation_message"] = "Unvollständige Positionsdaten (CANNOT VERIFY)"
                continue

            q_val = parse_numeric(qty, 0.0)
            up_val = parse_numeric(unit_price, 0.0)
            pe_val = parse_numeric(it.get("price_unit"), 1.0)
            if pe_val <= 0:
                pe_val = 1.0
            disc_raw = parse_numeric(
                it.get("discount_percent") if it.get("discount_percent") is not None else it.get("discount"),
                0.0,
            )
            disc_frac = (disc_raw / 100.0) if abs(disc_raw) >= 1.0 else disc_raw
            printed_val = round(parse_numeric(line_total, 0.0), 2)

            raw_calc = (q_val * up_val / pe_val) * (1.0 - disc_frac)
            if is_credit_note and printed_val < 0 and raw_calc > 0:
                expected_val = round(-raw_calc, 2)
            else:
                expected_val = round(raw_calc, 2)

            diff = round(printed_val - expected_val, 2)
            it["expected_total"] = expected_val
            it["difference"] = diff

            if abs(diff) > TOLERANCE:
                it["validation_status"] = "FAIL"
                msg_de = (
                    f"Position {pos_label} ({it.get('description', '')}): "
                    f"Erwartet {format_eur_de(expected_val)}, Gedruckt {format_eur_de(printed_val)}, "
                    f"Differenz {format_eur_de(diff)}"
                )
                it["validation_message"] = msg_de
                disc_obj = {
                    "line": pos_label,
                    "article_number": it.get("article_number", ""),
                    "description": it.get("description", ""),
                    "expected": expected_val,
                    "printed": printed_val,
                    "difference": diff,
                    "message": msg_de,
                }
                v1_discrepancies.append(disc_obj)
                line_flags.append(disc_obj)
            else:
                it["validation_status"] = "PASS"
                it["validation_message"] = "OK"

        if v1_discrepancies:
            summary_msgs = "; ".join(d["message"] for d in v1_discrepancies)
            checks.append({
                "code": "V1",
                "name": "Positionsberechnung (Menge x Einzelpreis / PE x (1 - Rabatt))",
                "name_en": "Line Item Math (qty x unit_price / price_unit x (1 - discount))",
                "status": "FAIL",
                "message": f"{len(v1_discrepancies)} Position(en) fehlerhaft: {summary_msgs}",
                "discrepancies": v1_discrepancies,
            })
        elif v1_unverifiable:
            checks.append({
                "code": "V1",
                "name": "Positionsberechnung (Menge x Einzelpreis / PE x (1 - Rabatt))",
                "name_en": "Line Item Math (qty x unit_price / price_unit x (1 - discount))",
                "status": "CANNOT VERIFY",
                "message": "Einzelne Positionen enthalten unvollständige Mengenangaben oder Preise.",
                "discrepancies": [],
            })
        else:
            checks.append({
                "code": "V1",
                "name": "Positionsberechnung (Menge x Einzelpreis / PE x (1 - Rabatt))",
                "name_en": "Line Item Math (qty x unit_price / price_unit x (1 - discount))",
                "status": "PASS",
                "message": f"Alle {len(line_items)} Positionen rechnerisch korrekt (Toleranz 0,01 €).",
                "discrepancies": [],
            })

    # ─────────────────────────────────────────────────────────────────────────
    # V2: per VAT rate, sum of line totals = net for that rate
    # ─────────────────────────────────────────────────────────────────────────
    if not line_items:
        checks.append({
            "code": "V2",
            "name": "Nettosumme je Steuersatz (Summe Positionen = Netto je USt-Satz)",
            "name_en": "Net Sum per VAT Rate (sum of line totals = net for rate)",
            "status": "CANNOT VERIFY",
            "message": "Keine Positionen extrahiert; Nettosumme je Steuersatz nicht prüfbar.",
            "discrepancies": [],
        })
    else:
        lines_by_rate: Dict[float, float] = {}
        for it in line_items:
            r = round(parse_numeric(it.get("vat_rate"), 19.0), 1)
            lt = parse_numeric(it.get("line_total"), 0.0)
            lines_by_rate[r] = round(lines_by_rate.get(r, 0.0) + lt, 2)

        effective_vb = list(vat_breakdown)
        if not effective_vb and len(lines_by_rate) == 1 and subtotal != 0.0:
            only_rate = next(iter(lines_by_rate.keys()))
            effective_vb = [{"rate": only_rate, "net": subtotal, "vat": tax_amount}]

        if not effective_vb:
            checks.append({
                "code": "V2",
                "name": "Nettosumme je Steuersatz (Summe Positionen = Netto je USt-Satz)",
                "name_en": "Net Sum per VAT Rate (sum of line totals = net for rate)",
                "status": "CANNOT VERIFY",
                "message": "Keine Nettobeträge je Steuersatz extrahiert.",
                "discrepancies": [],
            })
        else:
            v2_discs = []
            for vb in effective_vb:
                rate = round(parse_numeric(vb.get("rate"), 19.0), 1)
                printed_net = round(parse_numeric(vb.get("net"), 0.0), 2)
                expected_net = round(lines_by_rate.get(rate, 0.0), 2)
                diff = round(printed_net - expected_net, 2)
                if abs(diff) > TOLERANCE:
                    msg_de = (
                        f"Steuersatz {rate:g}%: Summe der Positionen (Erwartet) = {format_eur_de(expected_net)}, "
                        f"Gedruckter Nettobetrag = {format_eur_de(printed_net)}, Differenz = {format_eur_de(diff)}"
                    )
                    v2_discs.append({
                        "line": f"USt {rate:g}%",
                        "rate": rate,
                        "expected": expected_net,
                        "printed": printed_net,
                        "difference": diff,
                        "message": msg_de,
                    })
            if v2_discs:
                checks.append({
                    "code": "V2",
                    "name": "Nettosumme je Steuersatz (Summe Positionen = Netto je USt-Satz)",
                    "name_en": "Net Sum per VAT Rate (sum of line totals = net for rate)",
                    "status": "FAIL",
                    "message": "; ".join(d["message"] for d in v2_discs),
                    "discrepancies": v2_discs,
                })
            else:
                checks.append({
                    "code": "V2",
                    "name": "Nettosumme je Steuersatz (Summe Positionen = Netto je USt-Satz)",
                    "name_en": "Net Sum per VAT Rate (sum of line totals = net for rate)",
                    "status": "PASS",
                    "message": "Summe der Positionsbeträge entspricht für jeden Steuersatz exakt dem Nettobetrag.",
                    "discrepancies": [],
                })

    # ─────────────────────────────────────────────────────────────────────────
    # V3: per VAT rate, net x rate = VAT for that rate
    # ─────────────────────────────────────────────────────────────────────────
    effective_vb_v3 = list(vat_breakdown)
    if not effective_vb_v3 and subtotal != 0.0 and line_items:
        rates_set = {round(parse_numeric(it.get("vat_rate"), 19.0), 1) for it in line_items}
        if len(rates_set) == 1:
            effective_vb_v3 = [{"rate": next(iter(rates_set)), "net": subtotal, "vat": tax_amount}]

    if not effective_vb_v3:
        checks.append({
            "code": "V3",
            "name": "Steuerberechnung je Steuersatz (Netto x Steuersatz = USt)",
            "name_en": "VAT Calculation per Rate (net x rate = VAT)",
            "status": "CANNOT VERIFY",
            "message": "Steueraufschlüsselung je Steuersatz nicht extrahiert.",
            "discrepancies": [],
        })
    else:
        v3_discs = []
        for vb in effective_vb_v3:
            rate = round(parse_numeric(vb.get("rate"), 19.0), 1)
            net_val = round(parse_numeric(vb.get("net"), 0.0), 2)
            printed_vat = round(parse_numeric(vb.get("vat"), 0.0), 2)
            expected_vat = round(net_val * (rate / 100.0), 2)
            diff = round(printed_vat - expected_vat, 2)
            if abs(diff) > TOLERANCE:
                msg_de = (
                    f"Steuersatz {rate:g}% auf {format_eur_de(net_val)}: "
                    f"Erwartet {format_eur_de(expected_vat)}, Gedruckt {format_eur_de(printed_vat)}, "
                    f"Differenz {format_eur_de(diff)}"
                )
                v3_discs.append({
                    "line": f"USt {rate:g}%",
                    "rate": rate,
                    "expected": expected_vat,
                    "printed": printed_vat,
                    "difference": diff,
                    "message": msg_de,
                })
        if v3_discs:
            checks.append({
                "code": "V3",
                "name": "Steuerberechnung je Steuersatz (Netto x Steuersatz = USt)",
                "name_en": "VAT Calculation per Rate (net x rate = VAT)",
                "status": "FAIL",
                "message": "; ".join(d["message"] for d in v3_discs),
                "discrepancies": v3_discs,
            })
        else:
            checks.append({
                "code": "V3",
                "name": "Steuerberechnung je Steuersatz (Netto x Steuersatz = USt)",
                "name_en": "VAT Calculation per Rate (net x rate = VAT)",
                "status": "PASS",
                "message": "Umsatzsteuer je Steuersatz (Netto x Steuersatz) ist rechnerisch exakt.",
                "discrepancies": [],
            })

    # ─────────────────────────────────────────────────────────────────────────
    # V4: net total + VAT total = gross total
    # ─────────────────────────────────────────────────────────────────────────
    if subtotal == 0.0 and total_amount == 0.0:
        checks.append({
            "code": "V4",
            "name": "Gesamtbetragsprüfung (Netto gesamt + USt gesamt = Brutto gesamt)",
            "name_en": "Gross Total Check (net total + VAT total = gross total)",
            "status": "CANNOT VERIFY",
            "message": "Netto- oder Bruttogesamtbetrag wurde nicht extrahiert.",
            "discrepancies": [],
        })
    else:
        expected_gross = round(subtotal + tax_amount, 2)
        printed_gross = round(total_amount, 2)
        diff_v4 = round(printed_gross - expected_gross, 2)
        if abs(diff_v4) > TOLERANCE:
            msg_de = (
                f"Gesamtsumme: Erwartet {format_eur_de(expected_gross)} "
                f"({format_eur_de(subtotal)} + {format_eur_de(tax_amount)}), "
                f"Gedruckt {format_eur_de(printed_gross)}, Differenz {format_eur_de(diff_v4)}"
            )
            checks.append({
                "code": "V4",
                "name": "Gesamtbetragsprüfung (Netto gesamt + USt gesamt = Brutto gesamt)",
                "name_en": "Gross Total Check (net total + VAT total = gross total)",
                "status": "FAIL",
                "message": msg_de,
                "discrepancies": [{
                    "line": "Gesamtbetrag",
                    "expected": expected_gross,
                    "printed": printed_gross,
                    "difference": diff_v4,
                    "message": msg_de,
                }],
            })
        else:
            checks.append({
                "code": "V4",
                "name": "Gesamtbetragsprüfung (Netto gesamt + USt gesamt = Brutto gesamt)",
                "name_en": "Gross Total Check (net total + VAT total = gross total)",
                "status": "PASS",
                "message": f"Netto ({format_eur_de(subtotal)}) + USt ({format_eur_de(tax_amount)}) = Brutto ({format_eur_de(printed_gross)}).",
                "discrepancies": [],
            })

    # ─────────────────────────────────────────────────────────────────────────
    # V5: supplier VAT ID matches DE plus 9 digits (and VIES status check)
    # ─────────────────────────────────────────────────────────────────────────
    raw_vat_id = str(inv.get("vat_id") or "").strip()
    clean_vat_id = re.sub(r"\s+", "", raw_vat_id).upper()
    vies_status = str(inv.get("vies_status") or "unchecked").lower()

    EU_27_COUNTRIES = {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
        "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL",
        "PL", "PT", "RO", "SE", "SI", "SK",
    }
    supplier_country = str(inv.get("supplier_country") or "").strip().upper()
    is_de_or_missing = (not supplier_country or supplier_country == "DE")
    is_eu_non_de = (supplier_country in EU_27_COUNTRIES and supplier_country != "DE")

    if not is_de_or_missing and not is_eu_non_de:
        checks.append({
            "code": "V5",
            "name": "USt-IdNr. Format & VIES-Prüfung (Länderabhängig)",
            "name_en": "Supplier VAT ID Format & VIES (Country-Aware)",
            "status": "CANNOT VERIFY",
            "message": f"Nicht-EU-Lieferant ({supplier_country}): Kein EU-USt-IdNr.-Formatschema anwendbar (CANNOT VERIFY).",
            "discrepancies": [],
        })
    elif not clean_vat_id:
        checks.append({
            "code": "V5",
            "name": "USt-IdNr. Format & VIES-Prüfung (DE + 9 Ziffern)",
            "name_en": "Supplier VAT ID Format (DE + 9 digits) & VIES",
            "status": "CANNOT VERIFY",
            "message": "Keine USt-IdNr. des Lieferanten extrahiert (CANNOT VERIFY).",
            "discrepancies": [],
        })
    elif is_de_or_missing and not re.match(r"^DE\d{9}$", clean_vat_id):
        msg_de = f"Lieferanten-USt-IdNr. ungültig: Erwartet Format 'DE' + 9 Ziffern, Gedruckt '{raw_vat_id}'."
        checks.append({
            "code": "V5",
            "name": "USt-IdNr. Format & VIES-Prüfung (DE + 9 Ziffern)",
            "name_en": "Supplier VAT ID Format (DE + 9 digits) & VIES",
            "status": "FAIL",
            "message": msg_de,
            "discrepancies": [{
                "line": "USt-IdNr.",
                "expected": "DE + 9 Ziffern (DE999999999)",
                "printed": raw_vat_id,
                "difference": "Formatfehler",
                "message": msg_de,
            }],
        })
    elif is_eu_non_de and not re.match(r"^[A-Z]{2}[0-9A-Za-z]{8,12}$", clean_vat_id):
        msg_de = f"EU-Lieferanten-USt-IdNr. ({supplier_country}) ungültig: Erwartet Format '^[A-Z]{{2}}[0-9A-Za-z]{{8,12}}$', Gedruckt '{raw_vat_id}'."
        checks.append({
            "code": "V5",
            "name": "USt-IdNr. Format & VIES-Prüfung (EU)",
            "name_en": "Supplier VAT ID Format (EU) & VIES",
            "status": "FAIL",
            "message": msg_de,
            "discrepancies": [{
                "line": "USt-IdNr.",
                "expected": f"{supplier_country} + 8-12 alphanumerische Zeichen",
                "printed": raw_vat_id,
                "difference": "Formatfehler",
                "message": msg_de,
            }],
        })
    elif vies_status == "invalid":
        msg_de = f"USt-IdNr. '{clean_vat_id}' wurde vom EU-VIES-Dienst als UNGÜLTIG gemeldet."
        checks.append({
            "code": "V5",
            "name": "USt-IdNr. Format & VIES-Prüfung",
            "name_en": "Supplier VAT ID Format & VIES",
            "status": "FAIL",
            "message": msg_de,
            "discrepancies": [{
                "line": "VIES Check",
                "expected": "VIES valid",
                "printed": f"{clean_vat_id} (invalid)",
                "difference": "VIES-Prüfung fehlgeschlagen",
                "message": msg_de,
            }],
        })
    else:
        vies_note = "VIES bestätigt" if vies_status == "valid" else "VIES aktuell nicht erreichbar / ungeprüft"
        fmt_desc = "DE + 9 Ziffern" if is_de_or_missing else f"EU-Format ({supplier_country})"
        checks.append({
            "code": "V5",
            "name": "USt-IdNr. Format & VIES-Prüfung",
            "name_en": "Supplier VAT ID Format & VIES",
            "status": "PASS",
            "message": f"USt-IdNr. {clean_vat_id} entspricht {fmt_desc} ({vies_note}).",
            "discrepancies": [],
        })

    # ─────────────────────────────────────────────────────────────────────────
    # V6: dates are valid and due date is not before document date
    # ─────────────────────────────────────────────────────────────────────────
    raw_inv_date = str(inv.get("invoice_date") or "").strip()
    raw_due_date = str(inv.get("due_date") or "").strip()

    if not raw_inv_date or raw_inv_date.lower() in ("n/a", "none", ""):
        checks.append({
            "code": "V6",
            "name": "Datumsprüfung (Belegdatum gültig & Fälligkeit >= Belegdatum)",
            "name_en": "Date Validity & Due Date Not Before Document Date",
            "status": "CANNOT VERIFY",
            "message": "Belegdatum wurde nicht extrahiert.",
            "discrepancies": [],
        })
    else:
        inv_dt = _parse_iso_date_obj(raw_inv_date)
        if not inv_dt:
            msg_de = f"Belegdatum '{raw_inv_date}' ist kein gültiges Kalenderdatum."
            checks.append({
                "code": "V6",
                "name": "Datumsprüfung (Belegdatum gültig & Fälligkeit >= Belegdatum)",
                "name_en": "Date Validity & Due Date Not Before Document Date",
                "status": "FAIL",
                "message": msg_de,
                "discrepancies": [{
                    "line": "Belegdatum",
                    "expected": "Gültiges Datum (TT.MM.JJJJ)",
                    "printed": raw_inv_date,
                    "difference": "Ungültiges Datum",
                    "message": msg_de,
                }],
            })
        elif raw_due_date and raw_due_date.lower() not in ("n/a", "none", ""):
            due_dt = _parse_iso_date_obj(raw_due_date)
            if not due_dt:
                msg_de = f"Fälligkeitsdatum '{raw_due_date}' ist kein gültiges Kalenderdatum."
                checks.append({
                    "code": "V6",
                    "name": "Datumsprüfung (Belegdatum gültig & Fälligkeit >= Belegdatum)",
                    "name_en": "Date Validity & Due Date Not Before Document Date",
                    "status": "FAIL",
                    "message": msg_de,
                    "discrepancies": [{
                        "line": "Fälligkeitsdatum",
                        "expected": "Gültiges Datum (TT.MM.JJJJ)",
                        "printed": raw_due_date,
                        "difference": "Ungültiges Datum",
                        "message": msg_de,
                    }],
                })
            elif due_dt < inv_dt:
                days_diff = (due_dt - inv_dt).days
                msg_de = (
                    f"Fälligkeitsdatum ({due_dt.strftime('%d.%m.%Y')}) liegt {abs(days_diff)} Tag(e) "
                    f"vor dem Belegdatum ({inv_dt.strftime('%d.%m.%Y')})."
                )
                checks.append({
                    "code": "V6",
                    "name": "Datumsprüfung (Belegdatum gültig & Fälligkeit >= Belegdatum)",
                    "name_en": "Date Validity & Due Date Not Before Document Date",
                    "status": "FAIL",
                    "message": msg_de,
                    "discrepancies": [{
                        "line": "Fälligkeitsdatum",
                        "expected": f">= {inv_dt.strftime('%d.%m.%Y')}",
                        "printed": due_dt.strftime("%d.%m.%Y"),
                        "difference": f"{days_diff} Tage",
                        "message": msg_de,
                    }],
                })
            else:
                checks.append({
                    "code": "V6",
                    "name": "Datumsprüfung (Belegdatum gültig & Fälligkeit >= Belegdatum)",
                    "name_en": "Date Validity & Due Date Not Before Document Date",
                    "status": "PASS",
                    "message": f"Belegdatum ({inv_dt.strftime('%d.%m.%Y')}) und Fälligkeit ({due_dt.strftime('%d.%m.%Y')}) sind gültig.",
                    "discrepancies": [],
                })
        else:
            if is_credit_note:
                checks.append({
                    "code": "V6",
                    "name": "Datumsprüfung (Belegdatum gültig & Fälligkeit >= Belegdatum)",
                    "name_en": "Date Validity & Due Date Not Before Document Date",
                    "status": "PASS",
                    "message": f"Gutschriftsdatum ({inv_dt.strftime('%d.%m.%Y')}) ist gültig (Verrechnung mit nächster Zahlung).",
                    "discrepancies": [],
                })
            else:
                checks.append({
                    "code": "V6",
                    "name": "Datumsprüfung (Belegdatum gültig & Fälligkeit >= Belegdatum)",
                    "name_en": "Date Validity & Due Date Not Before Document Date",
                    "status": "CANNOT VERIFY",
                    "message": f"Belegdatum ({inv_dt.strftime('%d.%m.%Y')}) gültig, aber kein Fälligkeitsdatum extrahiert.",
                    "discrepancies": [],
                })

    # ─────────────────────────────────────────────────────────────────────────
    # V7: credit notes have all negative amounts, invoices all positive
    # ─────────────────────────────────────────────────────────────────────────
    if total_amount == 0.0 and not line_items:
        checks.append({
            "code": "V7",
            "name": "Vorzeichenprüfung (Rechnung positiv / Gutschrift negativ)",
            "name_en": "Sign Check (Invoices positive, Credit Notes negative)",
            "status": "CANNOT VERIFY",
            "message": "Keine Beträge für die Vorzeichenprüfung extrahiert.",
            "discrepancies": [],
        })
    else:
        v7_discs = []
        if is_credit_note:
            for idx, it in enumerate(line_items, start=1):
                lt = parse_numeric(it.get("line_total"), 0.0)
                if lt >= 0:
                    pos_l = str(it.get("pos") or idx)
                    v7_discs.append({
                        "line": f"Pos {pos_l}",
                        "expected": "< 0,00 € (Gutschrift)",
                        "printed": lt,
                        "difference": round(lt - (-abs(lt)), 2),
                        "message": f"Gutschrift Position {pos_l} hat positiven Betrag ({format_eur_de(lt)} statt negativ).",
                    })
            if subtotal > 0:
                v7_discs.append({
                    "line": "Nettobetrag",
                    "expected": "< 0,00 € (Gutschrift)",
                    "printed": subtotal,
                    "difference": round(subtotal * 2, 2),
                    "message": f"Gutschrift Nettobetrag ({format_eur_de(subtotal)}) muss negativ sein.",
                })
            if total_amount > 0:
                v7_discs.append({
                    "line": "Gutschriftsbetrag",
                    "expected": "< 0,00 € (Gutschrift)",
                    "printed": total_amount,
                    "difference": round(total_amount * 2, 2),
                    "message": f"Gutschriftsbetrag ({format_eur_de(total_amount)}) muss negativ sein.",
                })
        else:
            for idx, it in enumerate(line_items, start=1):
                lt = parse_numeric(it.get("line_total"), 0.0)
                if lt < 0:
                    pos_l = str(it.get("pos") or idx)
                    v7_discs.append({
                        "line": f"Pos {pos_l}",
                        "expected": "> 0,00 € (Rechnung)",
                        "printed": lt,
                        "difference": round(lt - abs(lt), 2),
                        "message": f"Rechnung Position {pos_l} hat negativen Betrag ({format_eur_de(lt)}).",
                    })
            if subtotal < 0:
                v7_discs.append({
                    "line": "Nettobetrag",
                    "expected": "> 0,00 € (Rechnung)",
                    "printed": subtotal,
                    "difference": round(subtotal - abs(subtotal), 2),
                    "message": f"Rechnung Nettobetrag ({format_eur_de(subtotal)}) muss positiv sein.",
                })
            if total_amount < 0:
                v7_discs.append({
                    "line": "Rechnungsbetrag",
                    "expected": "> 0,00 € (Rechnung)",
                    "printed": total_amount,
                    "difference": round(total_amount - abs(total_amount), 2),
                    "message": f"Rechnungsbetrag ({format_eur_de(total_amount)}) muss positiv sein.",
                })

        if v7_discs:
            checks.append({
                "code": "V7",
                "name": "Vorzeichenprüfung (Rechnung positiv / Gutschrift negativ)",
                "name_en": "Sign Check (Invoices positive, Credit Notes negative)",
                "status": "FAIL",
                "message": "; ".join(d["message"] for d in v7_discs),
                "discrepancies": v7_discs,
            })
        else:
            doc_label = "Gutschrift (alle Beträge negativ)" if is_credit_note else "Rechnung (alle Beträge positiv)"
            checks.append({
                "code": "V7",
                "name": "Vorzeichenprüfung (Rechnung positiv / Gutschrift negativ)",
                "name_en": "Sign Check (Invoices positive, Credit Notes negative)",
                "status": "PASS",
                "message": f"Vorzeichenprüfung für {doc_label} bestanden.",
                "discrepancies": [],
            })

    # ─────────────────────────────────────────────────────────────────────────
    # V8: mandatory fields per § 14 UStG (each missing field reported by name)
    # ─────────────────────────────────────────────────────────────────────────
    missing_fields: List[str] = []

    def _is_missing_str(val: Any) -> bool:
        s = str(val or "").strip()
        return not s or s.lower() in ("n/a", "unknown", "unknown vendor", "none", "null")

    if _is_missing_str(inv.get("vendor_name")):
        missing_fields.append("supplier_name (Lieferantenname)")
    if _is_missing_str(inv.get("vendor_address")):
        missing_fields.append("supplier_address (Lieferantenanschrift)")
    if _is_missing_str(inv.get("buyer_name")):
        missing_fields.append("buyer_name (Käufername)")
    if _is_missing_str(inv.get("buyer_address")):
        missing_fields.append("buyer_address (Käuferanschrift)")
    if _is_missing_str(inv.get("vat_id")) and _is_missing_str(inv.get("tax_number")):
        missing_fields.append("tax_number_or_vat_id (Steuernummer oder USt-IdNr.)")
    if _is_missing_str(inv.get("invoice_number")):
        missing_fields.append("invoice_number (Rechnungsnummer)")
    if _is_missing_str(inv.get("invoice_date")):
        missing_fields.append("document_date (Ausstellungsdatum)")
    if not line_items or any(
        _is_missing_str(it.get("description") or it.get("name")) or parse_numeric(it.get("quantity"), 0.0) == 0.0
        for it in line_items
    ):
        missing_fields.append("quantity_and_description_per_line (Menge und Bezeichnung je Position)")
    if subtotal == 0.0:
        missing_fields.append("net_amount (Nettobetrag / Entgelt)")
    has_vat_rate = bool(vat_breakdown) or any(it.get("vat_rate") is not None for it in line_items)
    if not has_vat_rate or (tax_amount == 0.0 and any(parse_numeric(it.get("vat_rate"), 0.0) > 0 for it in line_items)):
        missing_fields.append("vat_rate_and_vat_amount (Steuersatz und Steuerbetrag)")
    if total_amount == 0.0:
        missing_fields.append("gross_amount (Bruttobetrag / Gesamtbetrag)")

    if missing_fields:
        msg_de = f"Pflichtangaben gem. § 14 UStG fehlen: {', '.join(missing_fields)}"
        checks.append({
            "code": "V8",
            "name": "Pflichtangaben gem. § 14 UStG (Umsatzsteuergesetz)",
            "name_en": "Mandatory Fields per § 14 UStG (German VAT Act)",
            "status": "FAIL",
            "missing_fields": missing_fields,
            "message": msg_de,
            "discrepancies": [{
                "line": "§ 14 UStG",
                "expected": "Alle 11 Pflichtangaben vorhanden",
                "printed": f"Fehlend ({len(missing_fields)}): {', '.join(missing_fields)}",
                "difference": f"{len(missing_fields)} Pflichtfeld(er) fehlen",
                "message": msg_de,
            }],
        })
    else:
        checks.append({
            "code": "V8",
            "name": "Pflichtangaben gem. § 14 UStG (Umsatzsteuergesetz)",
            "name_en": "Mandatory Fields per § 14 UStG (German VAT Act)",
            "status": "PASS",
            "missing_fields": [],
            "message": "Alle Pflichtangaben gemäß § 14 Abs. 4 UStG vollständig vorhanden.",
            "discrepancies": [],
        })

    # ─────────────────────────────────────────────────────────────────────────
    # V9: Leitweg-ID present (Mandatory for XRechnung)
    # ─────────────────────────────────────────────────────────────────────────
    leitweg_id = str(inv.get("leitweg_id") or "").strip()
    if not leitweg_id or leitweg_id.lower() in ("n/a", "none", "null"):
        msg_de = (
            "Leitweg-ID (BT-10) fehlt. Die Leitweg-ID ist für XRechnung verpflichtend; "
            "ohne Leitweg-ID kann diese Rechnung NICHT als XRechnung versendet werden."
        )
        checks.append({
            "code": "V9",
            "name": "Leitweg-ID (BT-10 Pflichtfeld für XRechnung)",
            "name_en": "Leitweg-ID Present (Mandatory for XRechnung BT-10)",
            "status": "FAIL",
            "message": msg_de,
            "discrepancies": [{
                "line": "BT-10 Leitweg-ID",
                "expected": "Gültige Leitweg-ID (z.B. 04011000-1234512345-06)",
                "printed": "Fehlt (leer)",
                "difference": "Rechnung kann nicht als XRechnung versendet werden",
                "message": msg_de,
            }],
        })
        xrechnung_ready = False
    else:
        checks.append({
            "code": "V9",
            "name": "Leitweg-ID (BT-10 Pflichtfeld für XRechnung)",
            "name_en": "Leitweg-ID Present (Mandatory for XRechnung BT-10)",
            "status": "PASS",
            "message": f"Leitweg-ID '{leitweg_id}' vorhanden. Rechnung kann als XRechnung exportiert werden.",
            "discrepancies": [],
        })
        xrechnung_ready = True

    # Compute overall status:
    # Core financial & §14 UStG checks are V1..V8; V9 specifically gates XRechnung.
    core_fails = [c for c in checks if c["code"] != "V9" and c["status"] == "FAIL"]
    any_fails = [c for c in checks if c["status"] == "FAIL"]
    cannot_verify = [c for c in checks if c["status"] == "CANNOT VERIFY"]

    if core_fails:
        overall_status = "FAIL"
    elif any_fails:
        # Only V9 (Leitweg-ID for public sector XRechnung) failed, while V1-V8 passed
        overall_status = "WARN"
    elif cannot_verify:
        overall_status = "CANNOT VERIFY"
    else:
        overall_status = "PASS"

    passed_count = sum(1 for c in checks if c["status"] == "PASS")
    failed_count = sum(1 for c in checks if c["status"] == "FAIL")
    unverified_count = sum(1 for c in checks if c["status"] == "CANNOT VERIFY")

    return {
        "overall_status": overall_status,
        "core_math_valid": len(core_fails) == 0,
        "xrechnung_ready": xrechnung_ready and len(core_fails) == 0,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "cannot_verify_count": unverified_count,
        "total_checks": 9,
        "checks": checks,
        "line_flags": line_flags,
    }
