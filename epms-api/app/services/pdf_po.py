"""PDF generator for Purchase Orders using ReportLab."""
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

from app.models.po import PurchaseOrder
from app.services.pdf_template import (
    build_logo, footer_note_element, get_tmpl, header_note_element, terms_element,
)

_PRIMARY = colors.HexColor("#0A7C7C")
_LIGHT   = colors.HexColor("#F5F5F5")
_GRAY    = colors.HexColor("#737373")
_DARK    = colors.HexColor("#1A1A1A")


def _s(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


def generate_po_pdf(
    po: PurchaseOrder,
    company_name: str = "EPMS",
    pdf_templates: dict | None = None,
    logo_data_url: str | None = None,
) -> bytes:
    """Render an approved PurchaseOrder to PDF applying Admin Panel → PDF Templates settings."""
    tmpl = get_tmpl(pdf_templates, "po")
    buf = BytesIO()
    W = A4[0] - 40 * mm

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
    )

    co_style  = _s("co",  fontSize=18, textColor=_DARK, fontName="Helvetica-Bold")
    sub_style = _s("sub", fontSize=10, textColor=_GRAY, fontName="Helvetica")
    num_style = _s("num", fontSize=14, textColor=_PRIMARY, fontName="Helvetica-Bold", alignment=2)
    tag_style = _s("tag", fontSize=8,  textColor=_PRIMARY, fontName="Helvetica-Bold", alignment=2)
    lbl_style = _s("lbl", fontSize=8,  textColor=_GRAY,    fontName="Helvetica")
    val_style = _s("val", fontSize=9,  textColor=_DARK,    fontName="Helvetica")
    sec_style = _s("sec", fontSize=10, textColor=_PRIMARY, fontName="Helvetica-Bold", spaceAfter=4)

    story = []
    story.extend(header_note_element(tmpl.get("header_note", "")))

    # ── Header ────────────────────────────────────────────────────────────────
    logo = build_logo(logo_data_url, size_mm=27.0) if tmpl.get("show_logo", True) else None
    left_col = [logo, Spacer(1, 2 * mm), Paragraph("Purchase Order", co_style)] if logo else [Paragraph("Purchase Order", co_style)]
    header = Table(
        [[left_col, [Paragraph(po.number, num_style)]]],
        colWidths=[W * 0.6, W * 0.4],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [header, HRFlowable(width=W, color=_PRIMARY, thickness=1.5), Spacer(1, 4 * mm)]

    # ── Meta grid ─────────────────────────────────────────────────────────────
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _cell(label: str, value: str):
        return [Paragraph(label, lbl_style), Paragraph(value or "—", val_style)]

    meta = Table(
        [
            [_cell("Vendor",    po.vendor_name or "—"), _cell("PO Date",   now_str)],
            [_cell("PO Number", po.number      or "—"), _cell("Delivery",  str(po.expected_delivery or "—"))],
            [_cell("Delivery Address", po.delivery_address or "—"), _cell("Currency", po.currency or "CAD")],
        ],
        colWidths=[W * 0.55, W * 0.45],
    )
    meta.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [meta, Spacer(1, 5 * mm)]

    # ── Line items ────────────────────────────────────────────────────────────
    story.append(Paragraph("LINE ITEMS", sec_style))

    th_style = _s("th", fontSize=8, textColor=colors.white, fontName="Helvetica-Bold")
    td_style = _s("td", fontSize=8, textColor=_DARK, fontName="Helvetica", leading=11)
    td_r_style = _s("td_r", fontSize=8, textColor=_DARK, fontName="Helvetica",
                    leading=11, alignment=2)

    col_w = [8 * mm, W * 0.28, 26 * mm, 15 * mm, 14 * mm, 28 * mm, 28 * mm]
    headers = ["#", "Description", "Supplier ID", "Qty", "Unit", "Unit Price", "Line Total"]
    rows: list = [[Paragraph(h, th_style) for h in headers]]
    for i, item in enumerate(po.line_items, 1):
        rows.append([
            Paragraph(str(i),                              td_style),
            Paragraph(item.description,                    td_style),
            Paragraph(item.supplier_item_id or "",         td_style),
            Paragraph(str(item.qty),                       td_r_style),
            Paragraph(item.unit or "",                     td_style),
            Paragraph(f"{float(item.unit_price):,.2f}",    td_r_style),
            Paragraph(f"{float(item.line_total):,.2f}",    td_r_style),
        ])

    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  _PRIMARY),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, _LIGHT]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(tbl)

    # ── Totals ────────────────────────────────────────────────────────────────
    currency = po.currency or "CAD"
    totals = Table(
        [
            ["", "Subtotal", f"{currency} {float(po.subtotal):,.2f}"],
            ["", "Tax",      f"{currency} {float(po.tax_amount):,.2f}"],
            ["", "TOTAL",    f"{currency} {float(po.total):,.2f}"],
        ],
        colWidths=[W - 80 * mm, 30 * mm, 50 * mm],
    )
    totals.setStyle(TableStyle([
        ("ALIGN",      (1, 0), (-1, -1), "RIGHT"),
        ("FONTNAME",   (1, 2), (-1, 2),  "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("LINEABOVE",  (1, 2), (-1, 2),  0.8, _PRIMARY),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story += [Spacer(1, 3 * mm), totals, Spacer(1, 6 * mm)]

    # ── Terms & Conditions (from template) ───────────────────────────────────
    if tmpl.get("show_terms") and tmpl.get("terms_text"):
        story.extend(terms_element(tmpl["terms_text"]))

    # ── Footer ────────────────────────────────────────────────────────────────
    story += [
        HRFlowable(width=W, color=_GRAY, thickness=0.5),
        Spacer(1, 2 * mm),
        Paragraph(f"Generated by {company_name} · {now_str}", _s("ft", fontSize=7, textColor=_GRAY)),
    ]
    story.extend(footer_note_element(tmpl.get("footer_note", "")))

    doc.build(story)
    return buf.getvalue()
