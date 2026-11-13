"""The look of every printed document (2026-11-12, after the user's PO / invoice / packing-list
templates): the company's header with its logo, the title and number with the key facts on the
right, three cards (bill to / ship to / details), titled sections, a table whose head is the
document's colour, a totals box beside the notes, and on every page a footer with
"Page n of m". One colour per document: PO blue, invoice gold, packing list green.

Kept from the templates and made better: every table fits the page (the templates cut their
last columns off), headings are words (not "Quantity_Ordered"), amounts have thousands
separators, and the page count is printed.

Text that may hold Chinese (a ship-to name, a note) is drawn in WenQuanYi Zen Hei, embedded as
a TrueType subset — only for the CJK runs; Helvetica has no CJK glyphs and would draw black
boxes, and a CID font reference (not embedded) shows boxes on a reader without the font. When
the font file is missing the text falls back to Helvetica and it is logged once. Every value is
XML-escaped (Paragraph markup).
"""
from __future__ import annotations

import io
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Flowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)

CJK_FONT = "WQYZenHei"
CJK_PATHS = ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",)
_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]+")
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
        except Exception as e:  # noqa: BLE001 — a missing font never stops a document
            logger.warning("CJK font not available (%s): Chinese text would draw as boxes", e)
            _font_state["ok"] = False
    return _font_state["ok"]


def text(value: object) -> str:
    """Escaped Paragraph markup; CJK runs in the embedded font."""
    s = escape("" if value is None else str(value))
    if _CJK.search(s) and _cjk_font():
        s = _CJK.sub(lambda m: f'<font name="{CJK_FONT}">{m.group(0)}</font>', s)
    return s


# ── Values ──────────────────────────────────────────────────────────────────

def money(v: Decimal | None) -> str:
    return "" if v is None else f"{v:,.2f}"


def qty(v: object) -> str:
    """A quantity as people write it: 1,200 · 12.5 (no trailing zeros)."""
    if v is None or v == "":
        return ""
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    s = f"{d.normalize():,f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def day(v: date | None) -> str:
    return v.strftime("%d-%b-%Y") if v else ""


# ── Look ────────────────────────────────────────────────────────────────────

INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#6B7280")
NAVY = colors.HexColor("#14306E")   # the logo's blue
LINE = colors.HexColor("#D5DCE4")
ZEBRA = colors.HexColor("#F8FAFC")


@dataclass(frozen=True)
class Theme:
    accent: colors.Color
    tint: colors.Color


PO = Theme(colors.HexColor("#2B5F8E"), colors.HexColor("#EEF3F8"))
INVOICE = Theme(colors.HexColor("#A86F14"), colors.HexColor("#FBF6EC"))
PACKING = Theme(colors.HexColor("#22704E"), colors.HexColor("#EDF6F1"))
TRANSFER = Theme(colors.HexColor("#4A5568"), colors.HexColor("#F2F4F7"))

PAGE_W, PAGE_H = LETTER
MARGIN = 14 * mm
WIDTH = PAGE_W - 2 * MARGIN


def _style(size: float, *, bold: bool = False, color: colors.Color = INK, align: int = TA_LEFT, leading: float | None = None) -> ParagraphStyle:
    # leading set every time: ReportLab's default is 12 whatever the size (lines overlap above 10 pt).
    return ParagraphStyle(f"s{size}{bold}{align}", fontName="Helvetica-Bold" if bold else "Helvetica", fontSize=size,
                          leading=leading or round(size * 1.3, 1), textColor=color, alignment=align)


def p(value: object, size: float = 8.5, **kw) -> Paragraph:
    return Paragraph(text(value), _style(size, **kw))


def lines(values: Sequence[object], size: float = 8.5, **kw) -> Paragraph:
    """Several lines in one paragraph: each escaped, the breaks ours."""
    return Paragraph("<br/>".join(text(v) for v in values if v not in (None, "")), _style(size, **kw))


# ── Blocks ──────────────────────────────────────────────────────────────────

@dataclass
class Company:
    name: str
    address: list[str]
    email: str | None = None
    phone: str | None = None
    logo: bytes | None = None   # PNG / JPEG; none → the name alone


@dataclass
class Card:
    """A boxed block: an address (name, lines, small print such as the location code) or, on
    the details card, label / value pairs."""
    label: str
    name: str | None = None
    lines: list[str] = field(default_factory=list)
    small: list[str] = field(default_factory=list)
    facts: list[tuple[str, str | None]] = field(default_factory=list)


@dataclass
class Col:
    header: str
    weight: float            # share of the page width
    align: str = "LEFT"      # LEFT / RIGHT / CENTER
    strong: bool = False     # amounts: bold, in the logo's blue


Cell = str | tuple[str, str] | None   # text, or (text, small muted line under it)


def _logo(data: bytes | None, width: float) -> Image | None:
    if not data:
        return None
    try:
        r = ImageReader(io.BytesIO(data))
        w, h = r.getSize()
        return Image(io.BytesIO(data), width=width, height=width * h / w)
    except Exception as e:  # noqa: BLE001 — a bad logo never stops a document
        logger.warning("company logo not drawn (%s)", type(e).__name__)
        return None


def _facts(facts: Sequence[tuple[str, str | None]], width: float, theme: Theme) -> Table | None:
    rows = [[p(k.upper(), 6.5, bold=True, color=MUTED), p(v, 9, bold=True, color=NAVY, align=TA_RIGHT)] for k, v in facts if v]
    if not rows:
        return None
    t = Table(rows, colWidths=[width * 0.45, width * 0.55])
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
    return t


def header(company: Company, theme: Theme, title: str, number: str, facts: Sequence[tuple[str, str | None]] = (),
           party: Card | None = None) -> Table:
    """The top of the first page: the company (or, on a PO, the buyer) on the left; the title, the
    number and the key facts on the right; a rule in the document's colour under it."""
    left_w, right_w = WIDTH * 0.6, WIDTH * 0.4
    if party is not None:
        left: object = [p(party.label.upper(), 7, bold=True, color=theme.accent), Spacer(1, 1.5 * mm),
                        p(party.name, 15, bold=True, color=NAVY), Spacer(1, 1.5 * mm), lines(party.lines, 8.5),
                        Spacer(1, 1 * mm), lines(party.small, 7.5, color=MUTED)]
    else:
        contact = "  |  ".join(x for x in (company.email, company.phone) if x)
        block = [p(company.name.upper(), 14, bold=True, color=NAVY), Spacer(1, 1.5 * mm), lines(company.address, 8.5),
                 Spacer(1, 1.5 * mm), p(contact, 7.5, color=MUTED)]
        logo = _logo(company.logo, 34 * mm)
        if logo is not None:
            left = Table([[logo, block]], colWidths=[40 * mm, left_w - 40 * mm])
            left.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        else:
            left = block
    right: list[Flowable] = [p(title.upper(), 19, bold=True, color=NAVY, align=TA_RIGHT, leading=22),
                             p(number, 12, bold=True, color=theme.accent, align=TA_RIGHT), Spacer(1, 3 * mm)]
    f = _facts(facts, right_w - 4 * mm, theme)
    if f is not None:
        right.append(f)
    t = Table([[left, right]], colWidths=[left_w, right_w])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, 0), 0),
                           ("RIGHTPADDING", (-1, 0), (-1, 0), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                           ("LINEBELOW", (0, 0), (-1, 0), 2.2, theme.accent)]))
    return t


def section(title: str, subtitle: str | None, theme: Theme) -> Table:
    rows = [[p(title, 12, bold=True, color=NAVY)]] + ([[p(subtitle, 7, color=MUTED)]] if subtitle else [])
    t = Table(rows, colWidths=[WIDTH])
    t.setStyle(TableStyle([("LINEBEFORE", (0, 0), (0, -1), 3, theme.accent), ("LEFTPADDING", (0, 0), (-1, -1), 7),
                           ("TOPPADDING", (0, 0), (-1, -1), 0.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5)]))
    return t


def cards(cs: Sequence[Card], theme: Theme, *, shade_last: bool = True) -> Table:
    w = WIDTH / len(cs)
    cells = []
    for c in cs:
        flow: list[Flowable] = [p(c.label.upper(), 7, bold=True, color=INK), Spacer(1, 1.5 * mm)]
        if c.name:
            flow += [p(c.name, 10.5, bold=True), Spacer(1, 1.2 * mm)]
        if c.lines:
            flow.append(lines(c.lines, 8.3))
        if c.small:
            flow += [Spacer(1, 1.2 * mm), lines(c.small, 7, color=MUTED)]
        for k, v in c.facts:
            if v:
                flow += [Spacer(1, 1.2 * mm), p(k.upper(), 6.3, bold=True, color=MUTED), p(v, 8.8, bold=True)]
        cells.append(flow)
    t = Table([cells], colWidths=[w] * len(cs))
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
             ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
             ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]
    style += [("LINEBEFORE", (i, 0), (i, 0), 3, theme.accent if i in (0, len(cs) - 1) else NAVY) for i in range(len(cs))]
    if shade_last:
        style.append(("BACKGROUND", (-1, 0), (-1, 0), theme.tint))
    t.setStyle(TableStyle(style))
    return t


_ALIGN = {"LEFT": TA_LEFT, "RIGHT": TA_RIGHT, "CENTER": TA_CENTER}


def _cell(v: Cell, col: Col, bold: bool = False) -> object:
    a = _ALIGN[col.align]
    strong = col.strong or bold
    if isinstance(v, tuple):
        return [p(v[0], 8, bold=strong, color=NAVY if col.strong else INK, align=a), p(v[1], 6.5, color=MUTED, align=a)]
    return p(v, 8, bold=strong, color=NAVY if col.strong else INK, align=a)


def items(cols: Sequence[Col], rows: Sequence[Sequence[Cell]], theme: Theme, *, subtotals: Sequence[int] = (),
          foot: Sequence[Cell] | None = None) -> Table:
    """The document's table: its head in the document's colour (repeated on every page), light
    bands, `subtotals` (row indexes) and `foot` in bold on the tint."""
    total = sum(c.weight for c in cols)
    widths = [WIDTH * c.weight / total for c in cols]
    data: list[list[object]] = [[p(c.header, 7.3, bold=True, color=colors.white, align=_ALIGN[c.align]) for c in cols]]
    data += [[_cell(v, c, bold=i in subtotals) for v, c in zip(r, cols, strict=True)] for i, r in enumerate(rows)]
    if foot is not None:
        data.append([_cell(v, c, bold=True) for v, c in zip(foot, cols, strict=True)])
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), theme.accent), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE)]
    style += [("BACKGROUND", (0, i + 1), (-1, i + 1), ZEBRA) for i in range(len(rows)) if i % 2 == 1 and i not in subtotals]
    style += [("BACKGROUND", (0, i + 1), (-1, i + 1), theme.tint) for i in subtotals]
    if foot is not None:
        style += [("BACKGROUND", (0, -1), (-1, -1), theme.tint), ("LINEABOVE", (0, -1), (-1, -1), 1, theme.accent)]
    t.setStyle(TableStyle(style))
    return t


def closing(notes_title: str, notes: Sequence[str], box: Sequence[tuple[str, str]], theme: Theme, *, lead: str | None = None) -> Flowable:
    """The end of the document: the notes on the left, the totals box on the right (its last
    row the one that counts: the balance due, the PO total)."""
    left: list[Flowable] = [section(notes_title, None, theme), Spacer(1, 2.5 * mm)]
    if lead:
        left += [p(lead, 9.5, bold=True), Spacer(1, 1.5 * mm)]
    left.append(lines(notes, 7.8, color=MUTED))
    bw = WIDTH * 0.46
    rows = [[p(k.upper() if i < len(box) - 1 else k.upper(), 7.5 if i < len(box) - 1 else 9, bold=i == len(box) - 1,
               color=MUTED if i < len(box) - 1 else NAVY),
             p(v, 9 if i < len(box) - 1 else 12, bold=True, color=NAVY, align=TA_RIGHT)] for i, (k, v) in enumerate(box)]
    b = Table(rows, colWidths=[bw * 0.5, bw * 0.5])
    bs = [("BACKGROUND", (0, 0), (-1, -1), theme.tint), ("LINEABOVE", (0, 0), (-1, 0), 2.2, theme.accent),
          ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
          ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
    if len(box) > 1:
        bs.append(("LINEABOVE", (0, -1), (-1, -1), 1, theme.accent))
    b.setStyle(TableStyle(bs))
    t = Table([[left, b]], colWidths=[WIDTH - bw - 6 * mm, bw + 6 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("LEFTPADDING", (1, 0), (1, 0), 6 * mm)]))
    return KeepTogether([t])


def note(value: str, theme: Theme) -> Table:
    """A line the reader must not miss (a second 945 kept, a document not sent)."""
    t = Table([[p(value, 8.5, bold=True, color=INK)]], colWidths=[WIDTH])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7E6")),
                           ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#D97706")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


def gap(h: float = 5) -> Spacer:
    return Spacer(1, h * mm)


def _numbered(left: str, right: str, theme: Theme) -> type[rl_canvas.Canvas]:
    """A canvas that knows the page count: every page is kept, then each is drawn with its footer."""
    class Numbered(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._kept: list[dict] = []

        def showPage(self):  # noqa: N802 — ReportLab's name
            self._kept.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._kept)
            for state in self._kept:
                self.__dict__.update(state)
                self.saveState()
                self.setStrokeColor(theme.accent)
                self.setLineWidth(0.6)
                self.line(MARGIN, 11 * mm, PAGE_W - MARGIN, 11 * mm)
                self.setFont("Helvetica", 7)
                self.setFillColor(MUTED)
                self.drawString(MARGIN, 7 * mm, left)
                self.drawRightString(PAGE_W - MARGIN, 7 * mm, f"{right}  ·  Page {self._pageNumber} of {n}")
                self.restoreState()
                super().showPage()
            super().save()
    return Numbered


def build(story: list[Flowable], *, company: Company, title: str, theme: Theme) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, title=title, author=company.name, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=12 * mm, bottomMargin=16 * mm)
    doc.build(story, canvasmaker=_numbered(company.name, title, theme))
    return buf.getvalue()
