"""
zugferd.py - German ZUGFeRD / Factur-X (PDF/A-3 + embedded factur-x.xml) Generator.

Renders a German section 14 UStG compliant invoice PDF using reportlab and embeds
the exact EN 16931 XRechnung XML as `factur-x.xml` using pypdf, with AFRelationship
and Factur-X EN 16931 XMP metadata.
"""

import io
import re
from typing import Any, Dict, List, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    TextStringObject,
)

from validator import validate_invoice
from vector_store import parse_numeric, parse_german_date
from xrechnung import generate_xrechnung_xml


def _fmt_de_num(val: Any, decimals: int = 2) -> str:
    num = parse_numeric(val, 0.0)
    s = f"{num:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_de_eur(val: Any) -> str:
    return f"{_fmt_de_num(val, 2)} EUR"


def _fmt_de_date(val: Any) -> str:
    s = str(val or "").strip()
    if not s:
        return "-"
    iso = parse_german_date(s)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(iso or ""))
    if m:
        return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
    return s


def _build_facturx_xmp(invoice_number: str, vendor_name: str) -> bytes:
    safe_num = str(invoice_number or "Rechnung").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_vendor = str(vendor_name or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    xmp = f"""<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
      <pdfaid:part>3</pdfaid:part>
      <pdfaid:conformance>B</pdfaid:conformance>
    </rdf:Description>
    <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:title>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">Rechnung {safe_num}</rdf:li>
        </rdf:Alt>
      </dc:title>
      <dc:creator>
        <rdf:Seq>
          <rdf:li>{safe_vendor}</rdf:li>
        </rdf:Seq>
      </dc:creator>
    </rdf:Description>
    <rdf:Description rdf:about="" xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#">
      <fx:DocumentType>INVOICE</fx:DocumentType>
      <fx:DocumentFileName>factur-x.xml</fx:DocumentFileName>
      <fx:Version>1.0</fx:Version>
      <fx:ConformanceLevel>EN 16931</fx:ConformanceLevel>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    return xmp.encode("utf-8")


def _render_visual_pdf(inv: Dict[str, Any], effective_leitweg: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Rechnung {inv.get('invoice_number', '')}",
        author=str(inv.get("vendor_name") or "AutoInvoice"),
    )

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "InvTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#1C1917"),
        spaceAfter=4,
    )
    style_sub = ParagraphStyle(
        "InvSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#44403C"),
    )
    style_hdr = ParagraphStyle(
        "InvHdr",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#1C1917"),
    )
    style_body = ParagraphStyle(
        "InvBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#1C1917"),
    )
    style_note = ParagraphStyle(
        "InvNote",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#1C1917"),
        backColor=colors.HexColor("#F5F2EA"),
        borderPadding=6,
    )

    story: List[Any] = []

    doc_type = str(inv.get("document_type") or "invoice").lower()
    is_credit = "credit" in doc_type or "gutschrift" in doc_type
    heading_text = "GUTSCHRIFT (§ 14 UStG)" if is_credit else "RECHNUNG (§ 14 UStG)"
    story.append(Paragraph(heading_text, style_title))
    story.append(Paragraph("ZUGFeRD / EN 16931 Konvertierungsbeleg", style_sub))
    story.append(Spacer(1, 5 * mm))

    vendor_name = str(inv.get("vendor_name") or "-")
    vendor_addr = str(inv.get("vendor_address") or "-")
    vendor_vat = str(inv.get("vat_id") or "-")
    vendor_tax = str(inv.get("tax_number") or "")
    vendor_iban = str(inv.get("vendor_iban") or "")
    vendor_bic = str(inv.get("vendor_bic") or "")
    supplier_country = str(inv.get("supplier_country") or "").upper()

    buyer_name = str(inv.get("buyer_name") or "-")
    buyer_addr = str(inv.get("buyer_address") or "-")
    buyer_vat = str(inv.get("buyer_vat_id") or "-")

    seller_lines = [
        f"<b>Leistender Unternehmer (Lieferant)</b>",
        vendor_name,
        vendor_addr,
        f"USt-IdNr.: {vendor_vat}" + (f" ({supplier_country})" if supplier_country else ""),
    ]
    if vendor_tax:
        seller_lines.append(f"Steuernummer: {vendor_tax}")
    if vendor_iban:
        seller_lines.append(f"IBAN: {vendor_iban}" + (f" | BIC: {vendor_bic}" if vendor_bic else ""))

    buyer_lines = [
        f"<b>Leistungsempfänger (Käufer)</b>",
        buyer_name,
        buyer_addr,
        f"USt-IdNr.: {buyer_vat}",
        f"Leitweg-ID (BT-10): {effective_leitweg or '-'}",
    ]

    party_table = Table(
        [
            [
                Paragraph("<br/>".join(seller_lines), style_body),
                Paragraph("<br/>".join(buyer_lines), style_body),
            ]
        ],
        colWidths=[87 * mm, 87 * mm],
    )
    party_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D6D3CD")),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#D6D3CD")),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(party_table)
    story.append(Spacer(1, 4 * mm))

    inv_num = str(inv.get("invoice_number") or "-")
    inv_date = _fmt_de_date(inv.get("invoice_date"))
    deliv_date = _fmt_de_date(inv.get("delivery_date") or inv.get("invoice_date"))
    due_date = _fmt_de_date(inv.get("due_date"))

    meta_table = Table(
        [
            [
                Paragraph("<b>Rechnungsnummer</b>", style_hdr),
                Paragraph("<b>Rechnungsdatum</b>", style_hdr),
                Paragraph("<b>Leistungsdatum</b>", style_hdr),
                Paragraph("<b>Fälligkeitsdatum</b>", style_hdr),
            ],
            [
                Paragraph(inv_num, style_body),
                Paragraph(inv_date, style_body),
                Paragraph(deliv_date, style_body),
                Paragraph(due_date, style_body),
            ],
        ],
        colWidths=[46 * mm, 42 * mm, 43 * mm, 43 * mm],
    )
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F5F2EA")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D6D3CD")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6D3CD")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 5 * mm))

    # Line items table
    items = inv.get("line_items") if inv.get("line_items") is not None else inv.get("items") or []
    rows = [
        [
            Paragraph("<b>Pos.</b>", style_hdr),
            Paragraph("<b>Bezeichnung</b>", style_hdr),
            Paragraph("<b>Menge</b>", style_hdr),
            Paragraph("<b>ME</b>", style_hdr),
            Paragraph("<b>Einzelpreis</b>", style_hdr),
            Paragraph("<b>Rab. %</b>", style_hdr),
            Paragraph("<b>USt %</b>", style_hdr),
            Paragraph("<b>Gesamt (EUR)</b>", style_hdr),
        ]
    ]
    for idx, it in enumerate(items, start=1):
        pos = str(it.get("pos") or idx)
        desc = str(it.get("description") or it.get("name") or "-")
        qty = _fmt_de_num(it.get("quantity") if it.get("quantity") is not None else it.get("qty", 1), 2)
        unit = str(it.get("unit") or "Stk")
        up = _fmt_de_num(it.get("unit_price") if it.get("unit_price") is not None else it.get("rate", 0), 2)
        disc = _fmt_de_num(it.get("discount_percent") if it.get("discount_percent") is not None else it.get("discount", 0), 2)
        vat_r = _fmt_de_num(it.get("vat_rate", 0), 1)
        lt = _fmt_de_num(it.get("line_total") if it.get("line_total") is not None else it.get("total", 0), 2)
        rows.append(
            [
                Paragraph(pos, style_body),
                Paragraph(desc, style_body),
                Paragraph(qty, style_body),
                Paragraph(unit, style_body),
                Paragraph(up, style_body),
                Paragraph(disc, style_body),
                Paragraph(vat_r, style_body),
                Paragraph(lt, style_body),
            ]
        )

    items_table = Table(
        rows,
        colWidths=[12 * mm, 64 * mm, 16 * mm, 12 * mm, 22 * mm, 14 * mm, 13 * mm, 21 * mm],
        repeatRows=1,
    )
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFECE4")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#1C1917")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6D3CD")),
                ("ALIGN", (2, 1), (2, -1), "RIGHT"),
                ("ALIGN", (4, 1), (7, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(items_table)
    story.append(Spacer(1, 5 * mm))

    subtotal_val = parse_numeric(inv.get("subtotal"), 0.0)
    tax_val = parse_numeric(inv.get("tax_amount") if inv.get("tax_amount") is not None else inv.get("tax"), 0.0)
    total_val = parse_numeric(inv.get("total_amount"), 0.0)

    totals_table = Table(
        [
            [Paragraph("Nettobetrag gesamt", style_body), Paragraph(_fmt_de_eur(subtotal_val), style_body)],
            [Paragraph("Umsatzsteuer gesamt", style_body), Paragraph(_fmt_de_eur(tax_val), style_body)],
            [Paragraph("<b>Bruttobetrag gesamt</b>", style_hdr), Paragraph(f"<b>{_fmt_de_eur(total_val)}</b>", style_hdr)],
        ],
        colWidths=[54 * mm, 40 * mm],
        hAlign="RIGHT",
    )
    totals_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#1C1917")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D6D3CD")),
                ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#F5F2EA")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(totals_table)
    story.append(Spacer(1, 5 * mm))

    # Tax treatment legal note
    tax_treatment = str(inv.get("tax_treatment") or "standard").strip()
    if tax_treatment == "reverse_charge_13b":
        story.append(Paragraph("Steuerhinweis: Steuerschuldnerschaft des Leistungsempfängers (§ 13b UStG).", style_note))
        story.append(Spacer(1, 3 * mm))
    elif tax_treatment == "import_no_vat":
        story.append(Paragraph("Steuerhinweis: Steuerfreie Einfuhr / Drittland (ohne deutsche Umsatzsteuer).", style_note))
        story.append(Spacer(1, 3 * mm))

    # Original-invoice FX note matching BT-22 in xrechnung.py
    source_currency = str(inv.get("source_currency") or inv.get("currency") or "EUR").strip().upper()
    fx_rate_val = parse_numeric(inv.get("fx_rate"), 0.0)
    orig_gross_val = parse_numeric(inv.get("original_gross"), 0.0)
    fx_rate_source = str(inv.get("fx_rate_source") or "ECB").strip()
    fx_rate_date = str(inv.get("fx_rate_date") or "").strip()
    if source_currency != "EUR" and fx_rate_val > 0:
        fx_note = (
            f"Original: {orig_gross_val:.2f} {source_currency}, "
            f"{fx_rate_source} rate {fx_rate_val:.4f} {source_currency}/EUR on {fx_rate_date}"
        )
        story.append(Paragraph(f"Umrechnungshinweis (BT-22): {fx_note}", style_sub))

    doc.build(story)
    return buf.getvalue()


def _attach_facturx_and_xmp(
    visual_pdf_bytes: bytes,
    xml_bytes: bytes,
    invoice_number: str,
    vendor_name: str,
) -> bytes:
    reader = PdfReader(io.BytesIO(visual_pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    # Embed factur-x.xml via pypdf add_attachment, then enrich Filespec with AFRelationship & AF array
    writer.add_attachment("factur-x.xml", xml_bytes)

    root = writer._root_object
    names_dict = root.get("/Names")
    if names_dict is not None:
        names_obj = names_dict.get_object()
        ef_tree = names_obj.get("/EmbeddedFiles")
        if ef_tree is not None:
            ef_obj = ef_tree.get_object()
            names_arr = ef_obj.get("/Names")
            if names_arr and len(names_arr) >= 2:
                filespec_ref = names_arr[1]
                filespec_obj = filespec_ref.get_object()
                filespec_obj[NameObject("/AFRelationship")] = NameObject("/Alternative")
                filespec_obj[NameObject("/Desc")] = TextStringObject("Factur-X / XRechnung XML")
                ef_sub = filespec_obj.get("/EF")
                if ef_sub is not None:
                    f_stream_ref = ef_sub.get_object().get("/F")
                    if f_stream_ref is not None:
                        f_stream = f_stream_ref.get_object()
                        f_stream[NameObject("/Subtype")] = NameObject("/text#2Fxml")
                root[NameObject("/AF")] = ArrayObject([filespec_ref])

    # Attach Factur-X EN 16931 XMP metadata stream
    xmp_bytes = _build_facturx_xmp(invoice_number, vendor_name)
    xmp_stream = DecodedStreamObject()
    xmp_stream.set_data(xmp_bytes)
    xmp_stream[NameObject("/Type")] = NameObject("/Metadata")
    xmp_stream[NameObject("/Subtype")] = NameObject("/XML")
    xmp_ref = writer._add_object(xmp_stream)
    root[NameObject("/Metadata")] = xmp_ref

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def check_zugferd_validation_gate(inv: Dict[str, Any], effective_leitweg: str = "") -> Dict[str, Any]:
    """
    Run the 9-check validator and return gate decision:
    - Any FAIL blocks ZUGFeRD generation.
    - CANNOT VERIFY does not block and is returned as a warning.
    """
    inv_for_val = {
        **inv,
        "leitweg_id": effective_leitweg or inv.get("leitweg_id") or "",
    }
    val_report = validate_invoice(inv_for_val)
    failed = [c for c in val_report.get("checks", []) if c.get("status") == "FAIL"]
    warnings = [
        c
        for c in val_report.get("checks", [])
        if c.get("status") in ("CANNOT VERIFY", "WARN", "WARNING")
    ]
    return {
        "allowed": len(failed) == 0,
        "failed_checks": failed,
        "warnings": warnings,
        "validation_report": val_report,
    }


def generate_zugferd_pdf(
    inv: Dict[str, Any],
    default_leitweg_id: str = "",
) -> Tuple[bytes, str, List[Dict[str, Any]]]:
    """
    Validate, render German section 14 UStG PDF, and embed exact XRechnung XML
    as `factur-x.xml`. Raises ValueError if any validation check FAILS.
    Returns (pdf_bytes, filename, warning_checks).
    """
    raw_lw = str(inv.get("leitweg_id") or default_leitweg_id or "").strip()
    effective_leitweg = "" if raw_lw.lower() in ("", "n/a", "na", "none", "null", "-", "unknown", "unbekannt") else raw_lw

    gate = check_zugferd_validation_gate(inv, effective_leitweg=effective_leitweg)
    if not gate["allowed"]:
        failed_desc = " | ".join(
            f"{c.get('code')} ({c.get('status')}): {c.get('message', '')}"
            for c in gate["failed_checks"]
        )
        raise ValueError(f"ZUGFeRD-Erstellung blockiert - Prüfungen fehlgeschlagen: {failed_desc}")

    xml_content = generate_xrechnung_xml(inv, default_leitweg_id=effective_leitweg)
    xml_bytes = xml_content.encode("utf-8")

    visual_bytes = _render_visual_pdf({**inv, "leitweg_id": effective_leitweg}, effective_leitweg)
    pdf_bytes = _attach_facturx_and_xmp(
        visual_bytes,
        xml_bytes,
        invoice_number=str(inv.get("invoice_number") or "Rechnung"),
        vendor_name=str(inv.get("vendor_name") or ""),
    )

    clean_num = re.sub(r"[^A-Za-z0-9_.-]", "_", str(inv.get("invoice_number") or "Rechnung"))
    filename = f"{clean_num}_ZUGFeRD.pdf"
    return pdf_bytes, filename, gate["warnings"]
