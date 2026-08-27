"""PDF generator for Payment Applications using ReportLab."""
from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.pa import PaymentApplication
from app.services.pdf_template import (
    approvals_element, build_logo, footer_note_element, get_tmpl,
    header_note_element, qty_text, terms_element,
)

_PRIMARY = colors.HexColor("#0A7C7C")
_LIGHT = colors.HexColor("#F5F5F5")
_GRAY = colors.HexColor("#737373")
_DARK = colors.HexColor("#1A1A1A")


def _s(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


def generate_pa_pdf(
    pa: PaymentApplication,
    company_name: str = "EPMS",
    pdf_templates: dict | None = None,
    logo_data_url: str | None = None,
    requester_name: str | None = None,
    approvals: list[dict] | None = None,
) -> bytes:
    """Render a PaymentApplication to PDF applying Admin Panel → PDF Templates settings."""
    tmpl = get_tmpl(pdf_templates, "pa")
    buf = BytesIO()
    W = A4[0] - 40 * mm

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    # ReportLab's ParagraphStyle leading defaults to 12 regardless of fontSize, so an
    # 18pt company name overflowed its line box by 9.6pt and sat on the subtitle.
    co_style = _s("co", fontSize=15, leading=18, textColor=_DARK, fontName="Helvetica-Bold")
    sub_style = _s("sub", fontSize=10, leading=13, textColor=_GRAY, fontName="Helvetica")
    num_style = _s("num", fontSize=14, textColor=_PRIMARY, fontName="Helvetica-Bold", alignment=2)
    tag_style = _s("tag", fontSize=8, textColor=_PRIMARY, fontName="Helvetica-Bold", alignment=2)
    lbl_style = _s("lbl", fontSize=8, textColor=_GRAY, fontName="Helvetica")
    val_style = _s("val", fontSize=9, textColor=_DARK, fontName="Helvetica")
    sec_style = _s("sec", fontSize=10, textColor=_PRIMARY, fontName="Helvetica-Bold", spaceAfter=4)
    th_style = _s("th", fontSize=8, textColor=colors.white, fontName="Helvetica-Bold")
    td_style = _s("td", fontSize=8, textColor=_DARK, fontName="Helvetica")
    footer_style = _s("ft", fontSize=7, textColor=_GRAY, fontName="Helvetica")

    elements = []
    elements.extend(header_note_element(tmpl.get("header_note", "")))

    # ── Header ───────────────────────────────────────────────────────────────
    pa_type_label = "Prepayment Application" if pa.pa_type == "prepayment" else "Payment Application"
    logo = build_logo(logo_data_url) if tmpl.get("show_logo", True) else None
    left_col = [logo, Spacer(1, 1 * mm), Paragraph(company_name, co_style), Paragraph(pa_type_label, sub_style)] if logo else [Paragraph(company_name, co_style), Paragraph(pa_type_label, sub_style)]
    header = Table(
        [[left_col, [Paragraph(pa.pa_number, num_style), Paragraph("PAYMENT APPLICATION", tag_style)]]],
        colWidths=[W * 0.6, W * 0.4],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(header)
    elements.append(HRFlowable(width="100%", thickness=2, color=_PRIMARY, spaceAfter=6))

    # ── Meta grid ────────────────────────────────────────────────────────────
    def _cell(lbl: str, val: str | None) -> list:
        return [Paragraph(lbl, lbl_style), Paragraph(val or "—", val_style)]

    submitted_str = pa.submitted_at.strftime("%Y-%m-%d") if pa.submitted_at else "—"
    approved_str = pa.approved_at.strftime("%Y-%m-%d") if pa.approved_at else "—"

    meta = Table(
        [
            [*_cell("PA Number", pa.pa_number),    *_cell("Date Submitted", submitted_str)],
            [*_cell("Title", pa.title),             *_cell("Type", pa_type_label)],
            [*_cell("Vendor", pa.vendor_name),      *_cell("Currency", pa.currency)],
            [*_cell("PO Number", pa.po_number),     *_cell("Status", pa.status.replace("_", " ").title())],
            [*_cell("Applied By", requester_name),  *_cell("Date Approved", approved_str)],
        ],
        # Label columns widened from 0.12 to 0.14 (value narrowed 0.38 -> 0.36 to
        # compensate), matching pdf_pr.py's fix: "Date Submitted" needs ~55.14pt at
        # 8pt Helvetica but the 0.12*W - 8pt padding budget is only ~49.83pt, so it
        # already wrapped onto two lines before this change (and "Date Approved" /
        # "Applied By" would only make it worse). See task-3-report.md for the
        # verified fit under the widened budget.
        colWidths=[W * 0.14, W * 0.36, W * 0.14, W * 0.36],
    )
    meta.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, _LIGHT]),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(meta)
    elements.append(Spacer(1, 6 * mm))

    # ── Line items ───────────────────────────────────────────────────────────
    elements.append(Paragraph("Line Items", sec_style))

    col_headers = ["#", "Description", "Qty", "Unit", "Unit Price", "Total"]
    rows: list = [[Paragraph(h, th_style) for h in col_headers]]
    for i, item in enumerate(pa.line_items, 1):
        rows.append([
            Paragraph(str(i), td_style),
            Paragraph(item.description, td_style),
            Paragraph(qty_text(item.qty), td_style),
            Paragraph(item.unit, td_style),
            Paragraph(f"{item.unit_price:,.2f}", td_style),
            Paragraph(f"{item.line_total:,.2f}", td_style),
        ])

    col_widths = [W * 0.04, W * 0.48, W * 0.09, W * 0.09, W * 0.15, W * 0.15]
    items_tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _PRIMARY),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, _LIGHT]),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ALIGN",         (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(items_tbl)

    # ── Payment summary ──────────────────────────────────────────────────────
    elements.append(Spacer(1, 4 * mm))
    summary_rows = [
        [Paragraph("Subtotal", lbl_style), Paragraph(f"{pa.subtotal:,.2f}", val_style)],
    ]
    if pa.tax_amount:
        summary_rows.append([Paragraph("Tax", lbl_style), Paragraph(f"{pa.tax_amount:,.2f}", val_style)])
    if pa.shipping_amount:
        summary_rows.append([Paragraph("Shipping", lbl_style), Paragraph(f"{pa.shipping_amount:,.2f}", val_style)])
    if pa.other_charges:
        note = f" ({pa.other_charges_note})" if pa.other_charges_note else ""
        summary_rows.append([Paragraph(f"Other Charges{note}", lbl_style), Paragraph(f"{pa.other_charges:,.2f}", val_style)])

    summary_tbl = Table(summary_rows, colWidths=[W * 0.75, W * 0.25])
    summary_tbl.setStyle(TableStyle([
        ("ALIGN",         (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(summary_tbl)

    total_tbl = Table(
        [[
            Paragraph("Total Payment", _s("tl", fontSize=10, fontName="Helvetica-Bold", alignment=2)),
            Paragraph(
                f"{pa.currency} {pa.payment_amount:,.2f}",
                _s("tv", fontSize=12, textColor=_PRIMARY, fontName="Helvetica-Bold", alignment=2),
            ),
        ]],
        colWidths=[W * 0.75, W * 0.25],
    )
    total_tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEABOVE",     (0, 0), (-1, 0), 1, _PRIMARY),
    ]))
    elements.append(total_tbl)

    # ── Approvals ────────────────────────────────────────────────────────────
    elements.extend(approvals_element(approvals, W))

    # ── Prepayment details ───────────────────────────────────────────────────
    if pa.pa_type == "prepayment" and pa.prepayment_pct is not None:
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Prepayment Details", sec_style))
        prep_data = [
            *_cell("Prepayment %", f"{pa.prepayment_pct}%"),
            *_cell("Expected Settlement", str(pa.expected_settlement_date) if pa.expected_settlement_date else "—"),
        ]
        prep_tbl = Table([prep_data], colWidths=[W * 0.12, W * 0.38, W * 0.12, W * 0.38])
        prep_tbl.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(prep_tbl)

    # ── Notes ────────────────────────────────────────────────────────────────
    if pa.notes:
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Notes", sec_style))
        elements.append(Paragraph(pa.notes, val_style))

    # ── Footer ───────────────────────────────────────────────────────────────
    elements.append(Spacer(1, 8 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=_GRAY))
    elements.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · Status: Approved · {pa.pa_number}",
        footer_style,
    ))
    if tmpl.get("show_terms") and tmpl.get("terms_text"):
        elements.extend(terms_element(tmpl["terms_text"]))
    elements.extend(footer_note_element(tmpl.get("footer_note", "")))

    doc.build(elements)
    return buf.getvalue()
