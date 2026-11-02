"""The invoice email's three PDFs (Appendix A.12: invoice, PO, packing list),
plain ReportLab tables.

Text that may hold Chinese (a ship-to name, a note) is drawn in WenQuanYi
Zen Hei, embedded as a TrueType subset — only for the CJK runs; Helvetica's
standard font has no CJK glyphs and would draw black boxes, and a CID font
reference (not embedded) shows boxes on a reader without the font. When the
font file is missing the text falls back to Helvetica and it is logged once.
Every value is XML-escaped (Paragraph markup).
"""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)

CJK_FONT = "WQYZenHei"
CJK_PATHS = ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",)
_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]+")
_font_state: dict[str, bool] = {}


def _cjk_font() -> bool:
    if "ok" not in _font_state:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        path = next((p for p in CJK_PATHS if os.path.exists(p)), None)
        try:
            if path is None:
                raise FileNotFoundError("wqy-zenhei.ttc")
            pdfmetrics.registerFont(TTFont(CJK_FONT, path, subfontIndex=0))
            _font_state["ok"] = True
        except Exception as e:  # noqa: BLE001 — a missing font never stops an invoice
            logger.warning("CJK font not available (%s): Chinese text would draw as boxes", e)
            _font_state["ok"] = False
    return _font_state["ok"]


def text(value: object) -> str:
    """Escaped Paragraph markup; CJK runs in the embedded font."""
    s = escape("" if value is None else str(value))
    if _CJK.search(s) and _cjk_font():
        s = _CJK.sub(lambda m: f'<font name="{CJK_FONT}">{m.group(0)}</font>', s)
    return s


@dataclass
class Party:
    name: str | None = None
    lines: list[str] = field(default_factory=list)


@dataclass
class PdfLine:
    item_code: str | None
    description: str | None
    qty: object
    uom: str
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    extra: str | None = None  # lot / pallet on the packing list


@dataclass
class PdfDoc:
    title: str
    number: str
    when: date | None
    po: str | None
    order_number: int
    seller: Party
    buyer: Party
    ship_to: Party
    lines: list[PdfLine]
    terms: str | None = None
    currency: str = "CAD"
    total: Decimal | None = None
    priced: bool = True


def render(doc: PdfDoc) -> bytes:
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    p = lambda v, st="BodyText": Paragraph(text(v), styles[st])  # noqa: E731
    story = [p(f"{doc.title} {doc.number}", "Title"),
             p(f"Date: {doc.when or ''}    PO: {doc.po or ''}    Order: {doc.order_number}"
               + (f"    Terms: {doc.terms}" if doc.terms else "")),
             Spacer(1, 4 * mm)]
    def block(party: Party) -> Paragraph:  # each value escaped; the line breaks are ours
        return Paragraph("<br/>".join(text(x) for x in [party.name, *party.lines] if x), styles["BodyText"])
    parties = [[p("From", "Heading4"), p("Bill to", "Heading4"), p("Ship to", "Heading4")],
               [block(doc.seller), block(doc.buyer), block(doc.ship_to)]]
    story += [Table(parties, colWidths=[60 * mm] * 3), Spacer(1, 4 * mm)]
    head = ["Item", "Description", "Qty", "UOM"] + (["Unit price", "Amount"] if doc.priced else ["Lot / pallet"])
    rows = [[p(h, "Heading5") for h in head]]
    for ln in doc.lines:
        row = [p(ln.item_code), p(ln.description), p(ln.qty), p(ln.uom)]
        row += [p(f"{ln.unit_price:.2f}" if ln.unit_price is not None else ""),
                p(f"{ln.amount:.2f}" if ln.amount is not None else "")] if doc.priced else [p(ln.extra)]
        rows.append(row)
    widths = [28 * mm, 62 * mm, 18 * mm, 16 * mm, 26 * mm, 30 * mm] if doc.priced else [28 * mm, 70 * mm, 18 * mm, 16 * mm, 48 * mm]
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke)]))
    story.append(t)
    if doc.priced and doc.total is not None:
        story += [Spacer(1, 3 * mm), p(f"Total {doc.currency} {doc.total:.2f}", "Heading4")]
    SimpleDocTemplate(buf, pagesize=LETTER, title=f"{doc.title} {doc.number}",
                      leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm).build(story)
    return buf.getvalue()
