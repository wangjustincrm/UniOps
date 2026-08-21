"""PDF generator for Purchase Orders using ReportLab."""
from datetime import datetime, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
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
    signatory_name: str | None = None,
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

    # expected_delivery is a human-entered header override; NC-synced POs never
    # populate it (the ERP has no header delivery date). Fall back to the
    # earliest non-null line-level planned_arrival_date — NC's own "Delivery
    # Date" list column is that same rollup — but keep the header value's
    # precedence. This PDF goes to the vendor, so no internal provenance
    # marker is printed; only the resolved date is shown.
    if po.expected_delivery:
        delivery_str = str(po.expected_delivery)
    else:
        line_dates = [
            item.planned_arrival_date for item in po.line_items
            if item.planned_arrival_date
        ]
        delivery_str = str(min(line_dates)) if line_dates else "—"

    meta = Table(
        [
            [_cell("Vendor",    po.vendor_name or "—"), _cell("PO Date",   now_str)],
            [_cell("PO Number", po.number      or "—"), _cell("Delivery",  delivery_str)],
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

    # Columns are modelled as an ordered list of descriptors — header, fixed
    # width (None for the flex column), cell style, cell renderer, and
    # whether the column is optional. Headers, widths and body cells are all
    # derived from this one list, so the three can never drift apart. An
    # optional column is dropped from the document when every line's value
    # for it is empty (None or whitespace-only) — e.g. Material ID and
    # Supplier Item ID on POs that never populate them, or Sample on POs
    # with no buyer-supplied sample data. With zero line items every
    # optional column is (harmlessly) dropped too, since there is no line to
    # supply data for it; the table still renders with just its header row.
    #
    # Width budget (W = 170mm; every cell also loses 8mm to LEFTPADDING(4) +
    # RIGHTPADDING(4)): # 8, Material ID 18, Supplier Item ID 20, Qty 18,
    # UOM 13, Unit Price 18, Line Total 22, Sample 15. Description has no
    # fixed width — it always takes whatever remains of W, so dropping an
    # optional column widens Description automatically instead of leaving a
    # gap, and the arithmetic can't silently drift if a fixed width changes.
    class _Col:
        __slots__ = ("header", "width", "style", "cell", "optional")

        def __init__(self, header, width, style, cell, optional=False):
            self.header = header
            self.width = width      # None => flex (Description)
            self.style = style
            self.cell = cell        # (line_no, item) -> str
            self.optional = optional

    def _blank(value) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())

    all_columns = [
        _Col("#",                8 * mm,  td_style,   lambda i, item: str(i)),
        _Col("Material ID",      18 * mm, td_style,   lambda i, item: item.material_id or "",
             optional=True),
        _Col("Supplier Item ID", 20 * mm, td_style,   lambda i, item: item.supplier_item_id or "",
             optional=True),
        _Col("Description",      None,    td_style,   lambda i, item: item.description),
        _Col("Qty",               18 * mm, td_r_style, lambda i, item: str(item.qty)),
        _Col("UOM",               13 * mm, td_style,   lambda i, item: item.unit or ""),
        _Col("Unit Price",        18 * mm, td_r_style, lambda i, item: f"{float(item.unit_price):,.2f}"),
        _Col("Line Total",        22 * mm, td_r_style, lambda i, item: f"{float(item.line_total):,.2f}"),
        _Col("Sample",            15 * mm, td_style,   lambda i, item: getattr(item, "sample", None) or "",
             optional=True),
    ]

    def _has_data(col: "_Col") -> bool:
        return any(not _blank(col.cell(i, item)) for i, item in enumerate(po.line_items, 1))

    columns = [c for c in all_columns if not c.optional or _has_data(c)]

    desc_w = W - sum(c.width for c in columns if c.width is not None)
    headers = [c.header for c in columns]
    col_w = [c.width if c.width is not None else desc_w for c in columns]

    rows: list = [[Paragraph(h, th_style) for h in headers]]
    for i, item in enumerate(po.line_items, 1):
        rows.append([Paragraph(c.cell(i, item), c.style) for c in columns])

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

    # ── Incoterms ─────────────────────────────────────────────────────────────
    # Free text from the buyer-detail form — escape before handing to Paragraph,
    # which parses its content as mini-XML (unescaped "&"/"<"/">" raise or
    # silently swallow text; see the two escape() sites in this function).
    if po.incoterms:
        story += [
            Table([[Paragraph("Incoterms", lbl_style),
                    Paragraph(escape(po.incoterms).replace("\n", "<br/>"), val_style)]],
                  colWidths=[25 * mm, W - 25 * mm]),
            Spacer(1, 4 * mm),
        ]

    # ── Buyer Notes ───────────────────────────────────────────────────────────
    # NC owns purchase_orders.notes: every sync rewrites it with NC's memo plus
    # [NC Paid] / [NC Closed] markers meant for finance. Falling back to it on an
    # NC PO would print those internal markers on the vendor's copy, so only
    # non-NC POs fall back (that is where the Create PO page's "Buyer Notes /
    # Terms & Conditions" box lands — it never reached the PDF before).
    buyer_text = po.buyer_notes or (po.notes if po.source != "nc" else None)
    if buyer_text and buyer_text.strip():
        story += [
            Paragraph("BUYER NOTES", sec_style),
            Paragraph(escape(buyer_text.strip()).replace("\n", "<br/>"), val_style),
            Spacer(1, 4 * mm),
        ]

    # ── Terms & Conditions (from template) ───────────────────────────────────
    if tmpl.get("show_terms") and tmpl.get("terms_text"):
        story.extend(terms_element(tmpl["terms_text"]))

    # ── Signature Block (Type 1 POs only) ────────────────────────────────────
    # Countersigned commercial-document layout: our company on the left (named
    # signatory = the OPM, resolved by the caller — see po.py / po_attachments.py),
    # the vendor on the right, left blank for them to fill in by hand. No Date
    # row on either side (per spec). signatory_name is left None/blank by the
    # caller whenever the "opm" role has zero or more-than-one active holder,
    # so this never prints an arbitrarily-chosen name — the Title line still
    # prints regardless, since it is a fixed literal, not role-derived.
    if po.type == 1:
        sig_entity_style = _s("sig_entity", fontSize=10, textColor=_DARK, fontName="Helvetica-Bold")
        col_w = 80 * mm
        gap_w = W - 2 * col_w
        line_w = col_w - 8 * mm

        def _sig_col(entity_name: str | None, name_value: str | None, title_value: str) -> list:
            name_txt = escape(name_value) if name_value else ""
            title_txt = escape(title_value) if title_value else ""
            return [
                Paragraph(escape(entity_name) if entity_name else "—", sig_entity_style),
                Spacer(1, 13 * mm),
                HRFlowable(width=line_w, color=_GRAY, thickness=0.5),
                Spacer(1, 2 * mm),
                Paragraph(f"<b>Name:</b>&nbsp;&nbsp;&nbsp;{name_txt}", val_style),
                Paragraph(f"<b>Title:</b>&nbsp;&nbsp;&nbsp;{title_txt}", val_style),
            ]

        sig_table = Table(
            [[
                _sig_col(company_name, signatory_name, "Operation Manager"),
                "",
                _sig_col(po.vendor_name, None, ""),
            ]],
            colWidths=[col_w, gap_w, col_w],
        )
        sig_table.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story += [Spacer(1, 8 * mm), KeepTogether([sig_table])]

    # ── Footer ────────────────────────────────────────────────────────────────
    story += [
        HRFlowable(width=W, color=_GRAY, thickness=0.5),
        Spacer(1, 2 * mm),
        Paragraph(f"Generated by {company_name} · {now_str}", _s("ft", fontSize=7, textColor=_GRAY)),
    ]
    story.extend(footer_note_element(tmpl.get("footer_note", "")))

    doc.build(story)
    return buf.getvalue()
