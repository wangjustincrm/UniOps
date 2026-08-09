"""PDF generator for Goods Receipts using ReportLab."""
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

from app.models.gr import GoodsReceipt
from app.services.pdf_template import (
    build_logo, footer_note_element, get_tmpl, header_note_element, terms_element,
)

_PRIMARY = colors.HexColor("#0A7C7C")
_LIGHT = colors.HexColor("#F5F5F5")
_GRAY = colors.HexColor("#737373")
_DARK = colors.HexColor("#1A1A1A")


def _s(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


def generate_gr_pdf(
    gr: GoodsReceipt,
    company_name: str = "EPMS",
    pdf_templates: dict | None = None,
    logo_data_url: str | None = None,
    created_by_name: str | None = None,
    received_by: str | None = None,
    acknowledged_by: str | None = None,
) -> bytes:
    """Render a GoodsReceipt to PDF applying Admin Panel → PDF Templates settings."""
    tmpl = get_tmpl(pdf_templates, "gr")
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

    co_style = _s("co", fontSize=18, textColor=_DARK, fontName="Helvetica-Bold")
    sub_style = _s("sub", fontSize=10, textColor=_GRAY, fontName="Helvetica")
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
    logo = build_logo(logo_data_url) if tmpl.get("show_logo", True) else None
    left_col = [logo, Spacer(1, 1 * mm), Paragraph(company_name, co_style), Paragraph("Goods Receipt", sub_style)] if logo else [Paragraph(company_name, co_style), Paragraph("Goods Receipt", sub_style)]
    header = Table(
        [[left_col, [Paragraph(gr.number, num_style), Paragraph("GOODS RECEIPT", tag_style)]]],
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

    received_str = gr.received_at.strftime("%Y-%m-%d") if gr.received_at else "—"
    acknowledged_str = gr.acknowledged_at.strftime("%Y-%m-%d") if gr.acknowledged_at else "—"
    gr_type_label = "Physical" if gr.gr_type == "physical" else "Service"

    # crud/gr.py passes names already resolved against the users table; falling
    # back to the columns keeps the two legacy no-argument callers working.
    received_name = received_by if received_by is not None else gr.received_by
    acknowledged_name = acknowledged_by if acknowledged_by is not None else gr.acknowledged_by

    meta = Table(
        [
            [*_cell("GR Number", gr.number),         *_cell("Date Received", received_str)],
            [*_cell("Title", gr.title),               *_cell("Type", gr_type_label)],
            [*_cell("Vendor", gr.vendor_name),        *_cell("Currency", gr.currency)],
            [*_cell("PO Number", gr.po_number),       *_cell("PR Number", gr.pr_number)],
            [*_cell("Storage Location", gr.storage_location), *_cell("Acknowledged", acknowledged_str)],
        ],
        colWidths=[W * 0.12, W * 0.38, W * 0.12, W * 0.38],
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

    has_material = any(item.material_id for item in gr.line_items)
    has_discrepancy = any(item.condition != "good" for item in gr.line_items)

    col_headers = ["#", "Description"]
    if has_material:
        col_headers.append("Material ID")
    col_headers += ["Qty Ordered", "Qty Received", "Unit", "Unit Price", "Total"]
    if has_discrepancy:
        col_headers.append("Condition")

    rows: list = [[Paragraph(h, th_style) for h in col_headers]]
    for i, item in enumerate(gr.line_items, 1):
        row: list = [Paragraph(str(i), td_style), Paragraph(item.description, td_style)]
        if has_material:
            row.append(Paragraph(item.material_id or "", td_style))
        row += [
            Paragraph(str(item.qty_ordered.normalize()), td_style),
            Paragraph(str(item.qty_received.normalize()), td_style),
            Paragraph(item.unit, td_style),
            Paragraph(f"{item.unit_price:,.2f}", td_style),
            Paragraph(f"{item.line_total:,.2f}", td_style),
        ]
        if has_discrepancy:
            row.append(Paragraph(item.condition.capitalize(), td_style))
        rows.append(row)

    n_extra = (1 if has_material else 0) + (1 if has_discrepancy else 0)
    desc_w = W * (0.49 - 0.09 * n_extra)
    col_widths = (
        [W * 0.04, desc_w]
        + [W * 0.09] * (1 if has_material else 0)
        + [W * 0.09, W * 0.09, W * 0.07, W * 0.11, W * 0.11]
        + [W * 0.09] * (1 if has_discrepancy else 0)
    )

    items_tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _PRIMARY),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, _LIGHT]),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("ALIGN",         (-2, 0), (-1, -1), "RIGHT"),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(items_tbl)

    # ── Total ────────────────────────────────────────────────────────────────
    total_val = sum(item.line_total for item in gr.line_items)
    total_tbl = Table(
        [[
            Paragraph("Total Amount", _s("tl", fontSize=10, fontName="Helvetica-Bold", alignment=2)),
            Paragraph(
                f"{gr.currency} {total_val:,.2f}",
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

    # ── Notes ────────────────────────────────────────────────────────────────
    if gr.notes:
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Notes", sec_style))
        elements.append(Paragraph(gr.notes, val_style))

    # ── Created / Received / Acknowledged by ─────────────────────────────────
    if created_by_name or received_name or acknowledged_name:
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Signatures", sec_style))
        sig_data = []
        if created_by_name:
            sig_data.append([*_cell("Created By", created_by_name)])
        if received_name:
            sig_data.append([*_cell("Received By", received_name)])
        if acknowledged_name:
            sig_data.append([*_cell("Acknowledged By", acknowledged_name)])
        sig_tbl = Table(sig_data, colWidths=[W * 0.12, W * 0.38])
        sig_tbl.setStyle(TableStyle([
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(sig_tbl)

    # ── Footer ───────────────────────────────────────────────────────────────
    elements.append(Spacer(1, 8 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=_GRAY))
    elements.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · Status: Confirmed · {gr.number}",
        footer_style,
    ))
    if tmpl.get("show_terms") and tmpl.get("terms_text"):
        elements.extend(terms_element(tmpl["terms_text"]))
    elements.extend(footer_note_element(tmpl.get("footer_note", "")))

    doc.build(elements)
    return buf.getvalue()
