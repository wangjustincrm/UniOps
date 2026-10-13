"""Rendering an approved workplace label to PDF.

Deliberately plain. A workplace label is read at arm's length by somebody
holding a container, often in poor light, so the product name is large, the
signal word is larger, and the hazard statements are the only body text. Two
labels to an A4 sheet, laid out for a laminator.

**About the pictograms.** GHS pictograms are specific artwork — the flame, the
skull, the exploding chest — published by Health Canada for WHMIS 2015. Nothing
here draws one. The artwork is uploaded once per hazard class under Settings
and composed in; a label claiming a class whose artwork is missing cannot be
approved, so it cannot print. What this used to do was render the red diamond
and the hazard
class name inside it rather than the symbol, because the symbol files are not
in this repository and drawing an approximation of a regulated pictogram would
be worse than not drawing one. The label says so in the footer: the diamonds
are placed and named, and the official sticker goes on top. That is how most
plants already do it, and it is honest about what the system produced.

The QR is reportlab's own barcode widget rather than a new dependency, and it
points at the product record — so somebody holding the drum reaches the current
data sheet, which is the point of putting a code on it at all.

**Sizes, and why nothing is truncated any more.** Round 5, TC-21: "need a means
to resize the label as some of the data is being cut off and PPE required
freeform text is not being copied to the label."

Both were real. The PPE note was stored on the label and drawn nowhere. And the
layout was a fixed box with caps written into it — the first six hazard
statements, two lines each, five pictograms — so a product with seven statements
printed six and said nothing. **A workplace label that silently omits a hazard
statement is a WHMIS problem, not a cosmetic one.**

So the layout is measured before it is drawn. The same routine runs twice: once
to find out how tall the content is, then again to draw it. If it does not fit,
the type shrinks within a floor; if it still does not fit at the floor, rendering
*fails* and names what overflowed, so the answer is a bigger label rather than a
quietly incomplete one. Three sizes are offered, and the caller can ask for any
of them.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch, mm
from reportlab.pdfgen import canvas as pdfcanvas

from app.core.config import settings

GHS_RED = HexColor("#D0021B")

MARGIN_X = 10 * mm

# The QR's side at scale 1. Named because the text column's width is derived
# from it: the two have to move together or a pictogram ends up over the code,
# and the code is the only thing on the label that reaches the data sheet.
QR_SIDE = 26 * mm


class LabelTooLarge(ValueError):
    """The content does not fit the chosen label, even at the smallest type."""


@dataclass(frozen=True)
class LabelSize:
    code: str
    label: str
    width: float
    height: float
    # How many fit on one page, stacked.
    per_page: int
    # Type scale at this size. A small label is read closer, so the type does
    # not shrink in proportion to the box.
    scale: float
    # The page it is printed on: A4 unless this is label stock of its own.
    page: tuple[float, float] | None = None
    # ★ Never refused. Round 6, TC-09: "regardless of the information on the
    # SDS, the whmis requirements say the label must be on workplace bottles."
    # Asked for by name, this size shrinks the type until everything is on it
    # rather than saying it does not fit; "fit automatically" still moves up a
    # size first, while the type is readable.
    always_prints: bool = False


# The margin on label stock that is the page itself.
STOCK_MARGIN = 1.5 * mm

# ★ Named by what somebody is sticking it on, not by millimetres, because the
# person choosing is holding the container.
SIZES: tuple[LabelSize, ...] = (
    # Round 6, TC-09: "scale the small label to print on a 4 inch tall, by 6 inch
    # long label." A 4 × 6 roll prints a label per page, so the page *is* the
    # label — stacking them on A4 would print nothing a label printer can use.
    LabelSize("bottle", "Small — 4 × 6 in, a bottle or a spray can",
              6 * inch - 2 * STOCK_MARGIN, 4 * inch - 2 * STOCK_MARGIN, 1, 0.8,
              page=(6 * inch, 4 * inch), always_prints=True),
    LabelSize("drum", "Standard — a drum or a pail", 190 * mm, 128 * mm, 2, 1.0),
    LabelSize("sheet", "Large — a full page, for a tank or a store room",
              190 * mm, 258 * mm, 1, 1.25),
)
BY_SIZE = {s.code: s for s in SIZES}

# ★ The default is to fit, not to pick. A real label on the dev database — six
# hazard statements and eight precautionary ones — does not fit a half-A4 sheet
# at type you can read at arm's length. Before this round it printed anyway,
# six of each, silently. Refusing instead would be correct and would also make
# the Print button fail out of the box, which reads as broken.
#
# So FIT walks the stock from smallest up and uses the first that holds
# everything. The chooser is still there for somebody who knows what is in the
# printer, and the size actually used comes back in a header.
FIT = "fit"
DEFAULT_SIZE = FIT
CHOICES = (FIT,) + tuple(s.code for s in SIZES)

# How far the type may shrink before the label is declared too small. Below
# this a hazard statement stops being readable at arm's length, which is the
# whole function of the label.
MIN_SCALE_FACTOR = 0.72
# How far a size that always prints may go, as a last resort: still type, if
# small. Reached only when the small label is chosen by name for a product with
# more on its sheet than the stock holds.
LAST_RESORT_SCALE = 0.2


def _wrap(c: pdfcanvas.Canvas, text: str, font: str, size: float,
          width: float) -> list[str]:
    """Greedy wrap. reportlab has Paragraph for this, but a label is four short
    blocks and a full flowable document costs more than it saves."""
    c.setFont(font, size)
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if c.stringWidth(trial, font, size) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _pictogram(c: pdfcanvas.Canvas, x: float, y: float, size: float,
               artwork: bytes | None, name: str) -> None:
    """The official artwork for one hazard class, at `size` square.

    Where it is missing this draws a plainly empty slot rather than an
    approximation. A near-enough diamond is the worse failure: it looks like a
    pictogram, so nobody checks it, and a label that looks right while being
    non-compliant goes on a drum and stays there. An empty slot is a question
    somebody asks. Approval is blocked before it gets this far anyway — this is
    the second line, for a label approved before its artwork was removed.
    """
    if artwork is not None:
        c.drawImage(ImageReader(io.BytesIO(artwork)), x, y, width=size, height=size,
                    preserveAspectRatio=True, anchor="c", mask="auto")
        return

    c.saveState()
    c.setStrokeColor(HexColor("#999999"))
    c.setFillColor(white)
    c.setLineWidth(1)
    c.setDash(2, 2)
    c.rect(x, y, size, size, stroke=1, fill=1)
    c.setDash()
    c.setFillColor(HexColor("#999999"))
    c.setFont("Helvetica", 6)
    c.drawCentredString(x + size / 2, y + size / 2 + 3, "pictogram")
    c.drawCentredString(x + size / 2, y + size / 2 - 4, "not uploaded")
    c.setFont("Helvetica-Bold", 5.5)
    c.drawCentredString(x + size / 2, y + 2.5, name[:22])
    c.restoreState()


def _one_label(c: pdfcanvas.Canvas, label, chemical_id: str, top: float,
               artwork: dict[str, bytes] | None = None, *,
               size: LabelSize, scale: float, draw: bool = True,
               left: float = MARGIN_X) -> float:
    """Lay one label out, and return how much height it needed.

    ★ The same routine measures and draws. Two routines would drift, and a
    measurement that disagrees with the drawing is worse than no measurement:
    it would promise the content fits and then cut it off anyway.

    Two columns. The words on the left; on the right the QR code and, under it,
    the pictograms — round 6, TC-09: "to save room, move the hazard pictogram to
    beside or under the qr code." The pictograms used to take rows across the
    top of the words. The taller column decides the height.

    `draw=False` still calls `setFont` — `stringWidth` needs it — but puts no
    marks on the canvas.
    """
    y = top
    inner = left + 6 * mm
    pict = 20 * mm * scale
    pict_gap = 2.5 * mm * scale
    qr_side = QR_SIDE * scale
    # Two pictograms abreast, or the code, whichever is wider. ★ The text column
    # is derived from it, so nothing on the right can walk under the words —
    # the round-5 review caught a pictogram over the QR once already.
    right_w = max(qr_side, 2 * pict + pict_gap)
    right_x = left + size.width - right_w - 6 * mm
    text_w = right_x - 4 * mm - inner

    def at(points: float) -> float:
        return points * scale

    # ★ Line spacing shrinks with the type once the type is below the size's
    # own scale. It did not: the spacing was fixed in millimetres, so shrinking
    # the type only ever saved the lines that stopped wrapping, and a label with
    # a long list of statements could not be made to fit however small the type
    # went. Found making the small label always print (round 6, TC-09).
    squeeze = min(1.0, scale / size.scale)

    def line(text: str, font: str, pts: float, lead: float, colour=black) -> None:
        nonlocal y
        for wrapped in _wrap(c, text, font, at(pts), text_w):
            c.setFont(font, at(pts))
            if draw:
                c.setFillColor(colour)
                c.drawString(inner, y, wrapped)
                c.setFillColor(black)
            y -= at(lead) * squeeze

    if draw:
        c.setStrokeColor(black)
        c.setLineWidth(1)
        c.rect(left, top - size.height, size.width, size.height)

    y -= 12 * mm * scale

    # ── Product identifier, then the signal word ────────────────────────────
    line(label.product_identifier, "Helvetica-Bold", 20, 8 * mm / scale)

    if label.signal_word:
        c.setFont("Helvetica-Bold", at(24))
        if draw:
            c.setFillColor(GHS_RED if label.signal_word == "Danger" else black)
            c.drawString(inner, y, label.signal_word.upper())
            c.setFillColor(black)
        y -= 10 * mm * scale

    # ── Hazard statements ───────────────────────────────────────────────────
    # ★ No cap. Six of seven statements printed, with nothing saying so, is the
    # fault round 5 reported as "data is being cut off".
    for entry in list(label.hazard_statements or []):
        line(_statement(entry), "Helvetica-Bold", 9.5, 4.6 * mm / scale)

    y -= 1 * mm * scale
    for entry in list(label.precautionary_statements or []):
        line(_statement(entry), "Helvetica", 8.5, 4.1 * mm / scale)

    # ── PPE, then first aid ─────────────────────────────────────────────────
    # ★ Round 5: "PPE required freeform text is not being copied to the label."
    # Before first aid, because it is what somebody needs *before* opening the
    # container.
    if label.ppe_note:
        y -= 1.5 * mm * scale
        line("PPE REQUIRED", "Helvetica-Bold", 8.5, 4.2 * mm / scale)
        line(label.ppe_note, "Helvetica", 8.5, 4.1 * mm / scale)

    if label.first_aid:
        y -= 1.5 * mm * scale
        line("FIRST AID", "Helvetica-Bold", 8.5, 4.2 * mm / scale, colour=GHS_RED)
        line(label.first_aid, "Helvetica", 8.5, 4.1 * mm / scale)

    # ── Manufacturer: name and how to reach them ────────────────────────────
    # Round 6, TC-09: "the manufacturer's name contact must be shown."
    contact = getattr(label, "supplier_contact", None)
    if label.supplier or contact:
        y -= 2 * mm * scale
        line("MANUFACTURER", "Helvetica-Bold", 8, 4 * mm / scale)
        line(" · ".join(x for x in (label.supplier, contact) if x), "Helvetica", 8,
             4 * mm / scale, colour=HexColor("#444444"))

    text_height = top - y

    # ── Right: the QR to the product record, then the pictograms ────────────
    qr_top = top - 12 * mm * scale
    if draw:
        target = f"{settings.SAFETY_WEB_URL.rstrip('/')}/chemicals/{chemical_id}"
        widget = qr.QrCodeWidget(target)
        bounds = widget.getBounds()
        drawing = Drawing(qr_side, qr_side, transform=[
            qr_side / (bounds[2] - bounds[0]), 0, 0, qr_side / (bounds[3] - bounds[1]),
            0, 0])
        drawing.add(widget)
        qr_x = right_x + (right_w - qr_side) / 2
        renderPDF.draw(drawing, c, qr_x, qr_top - qr_side)
        c.setFont("Helvetica", at(6))
        c.drawCentredString(qr_x + qr_side / 2, qr_top - qr_side - 4 * mm * scale,
                            "Scan for the data sheet")
    ry = qr_top - qr_side - 7 * mm * scale
    names = list(label.pictograms or [])
    for i, name in enumerate(names):
        col = i % 2
        if col == 0 and i:
            ry -= pict + pict_gap
        if draw:
            key = str(name).strip().upper()
            _pictogram(c, right_x + col * (pict + pict_gap), ry - pict, pict,
                       (artwork or {}).get(key), str(name))
    if names:
        ry -= pict
    right_height = top - ry

    if draw:
        # ── Footer ──────────────────────────────────────────────────────────
        c.setFont("Helvetica-Oblique", at(6.5))
        c.setFillColor(HexColor("#666666"))
        approved = (f"{label.approved_at:%Y-%m-%d}" if label.approved_at else "—")
        footer = (f"Approved {approved} by {label.approved_by_name or 'HSE'} · "
                  "a safety data sheet for this product is available — scan the code")
        c.drawString(inner, top - size.height + 4 * mm * min(scale, 1), footer[:150])
        c.setFillColor(black)

    # The footer sits at the bottom of the box, so the content has to stop
    # above it rather than merely inside the box.
    return max(text_height, right_height) + 7 * mm * min(scale, 1)


def _statement(entry) -> str:
    """One hazard or precautionary statement as a line of text."""
    if isinstance(entry, dict):
        code = entry.get("code")
        text = entry.get("text") or ""
        return f"{code} {text}".strip() if code else str(text)
    return str(entry)


def render(label, *, chemical_id: str, copies: int = 2,
           artwork: dict[str, bytes] | None = None,
           size: str = DEFAULT_SIZE, _strict: bool = False) -> tuple[bytes, LabelSize]:
    """One or more labels to an A4 sheet, laid out for a laminator.

    ★ Measured before it is drawn. The type shrinks to fit within a floor, and
    if the content will not fit even there this raises `LabelTooLarge` naming
    what overflowed — because the alternative, which is what this did before, is
    a label missing a hazard statement and saying nothing about it.
    """
    if size == FIT:
        # Smallest first, each held to readable type — "fit" never shrinks a
        # label below that to stay small. The last is tried without a net so
        # its own overflow message is the one that reaches the caller.
        for candidate in SIZES[:-1]:
            try:
                return render(label, chemical_id=chemical_id, copies=copies,
                              artwork=artwork, size=candidate.code, _strict=True)
            except LabelTooLarge:
                continue
        return render(label, chemical_id=chemical_id, copies=copies,
                      artwork=artwork, size=SIZES[-1].code)

    chosen = BY_SIZE.get(size) or SIZES[1]
    page_w, page_height = chosen.page or A4
    on_stock = chosen.page is not None
    left = STOCK_MARGIN if on_stock else MARGIN_X
    first_top = page_height - (STOCK_MARGIN if on_stock else 10 * mm)

    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=(page_w, page_height))

    # ── Find a scale that fits ──────────────────────────────────────────────
    # Stepped rather than solved: the layout is not linear in the scale, because
    # wrapping changes the number of lines.
    usable = chosen.height - (2 * mm if on_stock else 10 * mm)

    def need(at_scale: float) -> float:
        return _one_label(c, label, chemical_id, first_top, artwork,
                          size=chosen, scale=at_scale, draw=False, left=left)

    scale = chosen.scale
    needed = need(scale)
    floor = chosen.scale * MIN_SCALE_FACTOR
    if chosen.always_prints and not _strict:
        floor = LAST_RESORT_SCALE
    while needed > usable and scale > floor:
        scale = max(floor, scale - 0.02)
        needed = need(scale)

    if needed > usable:
        over = (needed - usable) / mm
        raise LabelTooLarge(
            f"This label does not fit {chosen.label.split(' — ')[0].lower()} — "
            f"it needs about {over:.0f} mm more than the label has, with "
            f"{len(label.hazard_statements or [])} hazard statements and "
            f"{len(label.precautionary_statements or [])} precautionary ones. "
            f"Print it on a larger label, or shorten the PPE and first-aid notes."
        )

    per_page = chosen.per_page
    for index in range(max(1, copies)):
        slot = index % per_page
        if slot == 0 and index:
            c.showPage()
        top = first_top - slot * (chosen.height + 8 * mm)
        _one_label(c, label, chemical_id, top, artwork, size=chosen, scale=scale, left=left)

    c.showPage()
    c.save()
    return buffer.getvalue(), chosen
