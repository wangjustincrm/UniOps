"""PDF generator for Purchase Requests using ReportLab."""
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

from app.models.pr import PurchaseRequest
from app.services.pdf_template import (
    approvals_element, build_logo, footer_note_element, get_tmpl,
    header_note_element, terms_element,
)

PR_TYPES = {
    1: "Raw Materials",
    2: "Consumables",
    3: "Spare Parts",
    4: "Service",
    5: "Fixed Assets",
    6: "Software",
}

_PRIMARY = colors.HexColor("#0A7C7C")
_LIGHT = colors.HexColor("#F5F5F5")
_GRAY = colors.HexColor("#737373")
_DARK = colors.HexColor("#1A1A1A")


def _s(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


def generate_pr_pdf(
    pr: PurchaseRequest,
    company_name: str = "EPMS",
    pdf_templates: dict | None = None,
    logo_data_url: str | None = None,
    requester_name: str | None = None,
    approvals: list[dict] | None = None,
) -> bytes:
    """Render a PurchaseRequest to PDF applying Admin Panel → PDF Templates settings."""
    tmpl = get_tmpl(pdf_templates, "pr")
    buf = BytesIO()
    W = A4[0] - 40 * mm  # usable page width

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    # ── Shared styles ────────────────────────────────────────────────────────
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

    # ── Header note (from template) ───────────────────────────────────────────
    elements.extend(header_note_element(tmpl.get("header_note", "")))

    # ── Header ───────────────────────────────────────────────────────────────
    logo = build_logo(logo_data_url) if tmpl.get("show_logo", True) else None
    left_col = [logo, Spacer(1, 1 * mm), Paragraph(company_name, co_style), Paragraph("Purchase Request", sub_style)] if logo else [Paragraph(company_name, co_style), Paragraph("Purchase Request", sub_style)]
    header = Table(
        [[left_col, [Paragraph(pr.number, num_style), Paragraph("PURCHASE REQUEST", tag_style)]]],
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

    created_str = pr.created_at.strftime("%Y-%m-%d") if pr.created_at else "—"
    required_str = pr.required_by.strftime("%Y-%m-%d") if pr.required_by else "—"
    pr_type_str = PR_TYPES.get(pr.type, str(pr.type))
    submitted_str = pr.submitted_at.strftime("%Y-%m-%d") if pr.submitted_at else "—"

    meta = Table(
        [
            [*_cell("PR Number", pr.number),        *_cell("Date", created_str)],
            [*_cell("Title", pr.title),              *_cell("Type", pr_type_str)],
            [*_cell("Vendor", pr.vendor_name),       *_cell("Currency", pr.currency)],
            [*_cell("Department", pr.department_name), *_cell("Required By", required_str)],
            [*_cell("Cost Center", pr.cost_center_name), *_cell("Budget Code", pr.budget_code)],
            [*_cell("Requested By", requester_name), *_cell("Submitted", submitted_str)],
        ],
        # Label columns widened from 0.12 to 0.14 (value narrowed 0.38 -> 0.36 to
        # compensate) so "Requested By" (~50.2pt at 8pt Helvetica) fits without
        # wrapping onto two lines within the available cell width after padding.
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

    has_material = any(item.material_id for item in pr.line_items)
    # Always show Supplier ID for consistency with the Create PR page, which always exposes it.
    has_supplier = True

    col_headers = ["#", "Description"]
    if has_material:
        col_headers.append("Material ID")
    if has_supplier:
        col_headers.append("Supplier ID")
    col_headers += ["Qty", "Unit", "Unit Price", "Total"]

    rows: list = [[Paragraph(h, th_style) for h in col_headers]]
    for i, item in enumerate(pr.line_items, 1):
        row: list = [Paragraph(str(i), td_style), Paragraph(item.description, td_style)]
        if has_material:
            row.append(Paragraph(item.material_id or "", td_style))
        if has_supplier:
            row.append(Paragraph(item.supplier_item_id or "", td_style))
        row += [
            Paragraph(str(item.qty.normalize()), td_style),
            Paragraph(item.unit, td_style),
            Paragraph(f"{item.unit_price:,.2f}", td_style),
            Paragraph(f"{item.line_total:,.2f}", td_style),
        ]
        rows.append(row)

    n_extra = (1 if has_material else 0) + (1 if has_supplier else 0)
    desc_w = W * (0.32 - 0.07 * n_extra)
    col_widths = (
        [W * 0.04, desc_w]
        + [W * 0.10] * n_extra
        + [W * 0.08, W * 0.07, W * 0.13, W * 0.13]
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
    total_tbl = Table(
        [[
            Paragraph("Total Amount", _s("tl", fontSize=10, fontName="Helvetica-Bold", alignment=2)),
            Paragraph(
                f"{pr.currency} {pr.amount:,.2f}",
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

    # ── Notes ────────────────────────────────────────────────────────────────
    if pr.notes:
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Notes", sec_style))
        elements.append(Paragraph(pr.notes, val_style))

    # ── Delivery address ─────────────────────────────────────────────────────
    if pr.delivery_address:
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Delivery Address", sec_style))
        elements.append(Paragraph(pr.delivery_address, val_style))

    # ── Terms & Conditions (from template) ───────────────────────────────────
    if tmpl.get("show_terms") and tmpl.get("terms_text"):
        elements.extend(terms_element(tmpl["terms_text"]))

    # ── Footer ───────────────────────────────────────────────────────────────
    elements.append(Spacer(1, 8 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=_GRAY))
    elements.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · Status: Approved · {pr.number}",
        footer_style,
    ))
    elements.extend(footer_note_element(tmpl.get("footer_note", "")))

    doc.build(elements)
    return buf.getvalue()
