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
from reportlab.lib.units import mm
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
    # How many fit on one A4 sheet, stacked.
    per_page: int
    # Type scale at this size. A small label is read closer, so the type does
    # not shrink in proportion to the box.
    scale: float


# ★ Named by what somebody is sticking it on, not by millimetres, because the
# person choosing is holding the container.
SIZES: tuple[LabelSize, ...] = (
    LabelSize("bottle", "Small — a bottle or a spray can", 95 * mm, 62 * mm, 4, 0.52),
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
               size: LabelSize, scale: float, draw: bool = True) -> float:
    """Lay one label out, and return how much height it needed.

    ★ The same routine measures and draws. Two routines would drift, and a
    measurement that disagrees with the drawing is worse than no measurement:
    it would promise the content fits and then cut it off anyway.

    `draw=False` still calls `setFont` — `stringWidth` needs it — but puts no
    marks on the canvas.
    """
    left = MARGIN_X
    y = top
    inner = left + 6 * mm
    # ★ Derived from the QR rather than a fixed 34 mm. The code's width scales
    # with the type (`QR_SIDE * scale` below) and the reservation did not, so the
    # clearance was a coincidence — 1.5 mm at the largest size, and gone the
    # moment anybody nudged that scale or made the code bigger. Raised in review
    # by uniops-41 after the overlap it had already caught once.
    text_w = size.width - 12 * mm - (QR_SIDE * scale + 8 * mm)

    def at(points: float) -> float:
        return points * scale

    def line(text: str, font: str, pts: float, lead: float, colour=black) -> None:
        nonlocal y
        for wrapped in _wrap(c, text, font, at(pts), text_w):
            c.setFont(font, at(pts))
            if draw:
                c.setFillColor(colour)
                c.drawString(inner, y, wrapped)
                c.setFillColor(black)
            y -= at(lead)

    if draw:
        c.setStrokeColor(black)
        c.setLineWidth(1)
        c.rect(left, top - size.height, size.width, size.height)

    y -= 12 * mm * scale

    # ── Product identifier ──────────────────────────────────────────────────
    line(label.product_identifier, "Helvetica-Bold", 20, 8 * mm / scale)

    # ── Pictograms, then the signal word ────────────────────────────────────
    # WHMIS 2015 orders the elements: product identifier, pictograms, signal
    # word, hazard statements, precautionary statements, supplier identifier.
    if label.pictograms:
        side = 20 * mm * scale
        x = inner
        # ★ The text column's width, not the label's. The QR sits in the
        # top-right corner, and taking the caps off the pictogram row let the
        # sixth one walk underneath it — on a label whose whole purpose on the
        # drum is that somebody can scan the code and reach the current data
        # sheet. A pictogram over the QR breaks the entry point to the SDS, so
        # the row wraps at the same boundary the text does. Raised in review by
        # uniops-41, against a cap I had removed.
        room = text_w
        for name in list(label.pictograms):
            if x + side > left + room:          # wrap onto the next row
                x = inner
                y -= side + 3 * mm * scale
            key = str(name).strip().upper()
            if draw:
                _pictogram(c, x, y - side, side, (artwork or {}).get(key), str(name))
            x += side + 4 * mm * scale
        y -= side + 5 * mm * scale

    if label.signal_word:
        c.setFont("Helvetica-Bold", at(24))
        if draw:
            c.setFillColor(GHS_RED if label.signal_word == "Danger" else black)
            c.drawString(inner, y, label.signal_word.upper())
            c.setFillColor(black)
        y -= 10 * mm * scale

    # ── Hazard statements ───────────────────────────────────────────────────
    # ★ No cap. Six of seven statements printed, with nothing saying so, is the
    # fault this round reported as "data is being cut off".
    for entry in list(label.hazard_statements or []):
        line(_statement(entry), "Helvetica-Bold", 9.5, 4.6 * mm / scale)

    y -= 1 * mm * scale
    for entry in list(label.precautionary_statements or []):
        line(_statement(entry), "Helvetica", 8.5, 4.1 * mm / scale)

    # ── PPE, then first aid ─────────────────────────────────────────────────
    # ★ Round 5: "PPE required freeform text is not being copied to the label."
    # It was stored on the label and drawn nowhere. Before first aid, because it
    # is what somebody needs *before* opening the container.
    if label.ppe_note:
        y -= 1.5 * mm * scale
        line("PPE REQUIRED", "Helvetica-Bold", 8.5, 4.2 * mm / scale)
        line(label.ppe_note, "Helvetica", 8.5, 4.1 * mm / scale)

    if label.first_aid:
        y -= 1.5 * mm * scale
        line("FIRST AID", "Helvetica-Bold", 8.5, 4.2 * mm / scale, colour=GHS_RED)
        line(label.first_aid, "Helvetica", 8.5, 4.1 * mm / scale)

    # ── Supplier identifier ─────────────────────────────────────────────────
    if label.supplier:
        y -= 2 * mm * scale
        line(label.supplier, "Helvetica", 8, 4 * mm / scale,
             colour=HexColor("#444444"))

    content_height = top - y

    # ── QR to the product record ────────────────────────────────────────────
    if draw:
        target = f"{settings.SAFETY_WEB_URL.rstrip('/')}/chemicals/{chemical_id}"
        widget = qr.QrCodeWidget(target)
        bounds = widget.getBounds()
        side = QR_SIDE * scale
        drawing = Drawing(side, side, transform=[
            side / (bounds[2] - bounds[0]), 0, 0, side / (bounds[3] - bounds[1]),
            0, 0])
        drawing.add(widget)
        qr_x = left + size.width - side - 6 * mm
        renderPDF.draw(drawing, c, qr_x, top - side - 12 * mm * scale)
        c.setFont("Helvetica", at(6))
        c.drawCentredString(qr_x + side / 2, top - side - 16 * mm * scale,
                            "Scan for the data sheet")

        # ── Footer ──────────────────────────────────────────────────────────
        c.setFont("Helvetica-Oblique", at(6.5))
        c.setFillColor(HexColor("#666666"))
        approved = (f"{label.approved_at:%Y-%m-%d}" if label.approved_at else "—")
        footer = (f"Approved {approved} by {label.approved_by_name or 'HSE'} · "
                  "a safety data sheet for this product is available — scan the code")
        c.drawString(inner, top - size.height + 4 * mm, footer[:150])
        c.setFillColor(black)

    # The footer sits at the bottom of the box, so the content has to stop
    # above it rather than merely inside the box.
    return content_height + 7 * mm


def _statement(entry) -> str:
    """One hazard or precautionary statement as a line of text."""
    if isinstance(entry, dict):
        code = entry.get("code")
        text = entry.get("text") or ""
        return f"{code} {text}".strip() if code else str(text)
    return str(entry)


def render(label, *, chemical_id: str, copies: int = 2,
           artwork: dict[str, bytes] | None = None,
           size: str = DEFAULT_SIZE) -> tuple[bytes, LabelSize]:
    """One or more labels to an A4 sheet, laid out for a laminator.

    ★ Measured before it is drawn. The type shrinks to fit within a floor, and
    if the content will not fit even there this raises `LabelTooLarge` naming
    what overflowed — because the alternative, which is what this did before, is
    a label missing a hazard statement and saying nothing about it.
    """
    if size == FIT:
        # Smallest first. The last one is tried without a net so its own
        # overflow message is the one that reaches the caller.
        for candidate in SIZES[:-1]:
            try:
                return render(label, chemical_id=chemical_id, copies=copies,
                              artwork=artwork, size=candidate.code)
            except LabelTooLarge:
                continue
        return render(label, chemical_id=chemical_id, copies=copies,
                      artwork=artwork, size=SIZES[-1].code)

    chosen = BY_SIZE.get(size) or SIZES[1]

    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=A4)
    _, page_height = A4

    # ── Find a scale that fits ──────────────────────────────────────────────
    # Stepped rather than solved: the layout is not linear in the scale, because
    # wrapping changes the number of lines.
    usable = chosen.height - 10 * mm
    scale = chosen.scale
    needed = _one_label(c, label, chemical_id, page_height - 10 * mm, artwork,
                        size=chosen, scale=scale, draw=False)
    floor = chosen.scale * MIN_SCALE_FACTOR
    while needed > usable and scale > floor:
        scale = max(floor, scale - 0.04)
        needed = _one_label(c, label, chemical_id, page_height - 10 * mm, artwork,
                            size=chosen, scale=scale, draw=False)

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
        top = page_height - 10 * mm - slot * (chosen.height + 8 * mm)
        _one_label(c, label, chemical_id, top, artwork, size=chosen, scale=scale)

    c.showPage()
    c.save()
    return buffer.getvalue(), chosen
