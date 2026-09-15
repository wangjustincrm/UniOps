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
"""
from __future__ import annotations

import io

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

LABEL_W = 190 * mm
LABEL_H = 128 * mm
MARGIN_X = 10 * mm


def _wrap(c: pdfcanvas.Canvas, text: str, font: str, size: int, width: float) -> list[str]:
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
               artwork: dict[str, bytes] | None = None) -> None:
    left = MARGIN_X
    y = top

    c.setStrokeColor(black)
    c.setLineWidth(1)
    c.rect(left, top - LABEL_H, LABEL_W, LABEL_H)

    inner = left + 6 * mm
    text_w = LABEL_W - 12 * mm - 30 * mm  # room for the QR on the right
    y -= 12 * mm

    # ── Product identifier ──────────────────────────────────────────────────
    for line in _wrap(c, label.product_identifier, "Helvetica-Bold", 20, text_w)[:2]:
        c.setFont("Helvetica-Bold", 20)
        c.setFillColor(black)
        c.drawString(inner, y, line)
        y -= 8 * mm

    # ── Pictograms, then the signal word ────────────────────────────────────
    # WHMIS 2015 orders the elements: product identifier, pictograms, signal
    # word, hazard statements, precautionary statements, supplier identifier.
    # The supplier used to sit under the product name at the top, which put the
    # least urgent thing on the label above the two that carry the warning.
    if label.pictograms:
        size = 20 * mm
        x = inner
        for name in list(label.pictograms)[:5]:
            key = str(name).strip().upper()
            _pictogram(c, x, y - size, size, (artwork or {}).get(key), str(name))
            x += size + 4 * mm
        y -= size + 5 * mm

    if label.signal_word:
        c.setFont("Helvetica-Bold", 24)
        c.setFillColor(GHS_RED if label.signal_word == "Danger" else black)
        c.drawString(inner, y, label.signal_word.upper())
        c.setFillColor(black)
        y -= 10 * mm

    # ── Hazard statements ───────────────────────────────────────────────────
    for entry in list(label.hazard_statements or [])[:6]:
        code = entry.get("code") if isinstance(entry, dict) else None
        text = entry.get("text") if isinstance(entry, dict) else str(entry)
        line = f"{code} {text}".strip() if code else str(text)
        for wrapped in _wrap(c, line, "Helvetica-Bold", 9.5, text_w)[:2]:
            c.setFont("Helvetica-Bold", 9.5)
            c.drawString(inner, y, wrapped)
            y -= 4.6 * mm

    y -= 1 * mm
    for entry in list(label.precautionary_statements or [])[:6]:
        code = entry.get("code") if isinstance(entry, dict) else None
        text = entry.get("text") if isinstance(entry, dict) else str(entry)
        line = f"{code} {text}".strip() if code else str(text)
        for wrapped in _wrap(c, line, "Helvetica", 8.5, text_w)[:2]:
            c.setFont("Helvetica", 8.5)
            c.drawString(inner, y, wrapped)
            y -= 4.1 * mm

    if label.first_aid:
        y -= 1.5 * mm
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColor(GHS_RED)
        c.drawString(inner, y, "FIRST AID")
        c.setFillColor(black)
        y -= 4.2 * mm
        for wrapped in _wrap(c, label.first_aid, "Helvetica", 8.5, text_w)[:2]:
            c.setFont("Helvetica", 8.5)
            c.drawString(inner, y, wrapped)
            y -= 4.1 * mm

    # ── Supplier identifier ─────────────────────────────────────────────────
    if label.supplier:
        y -= 2 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(HexColor("#444444"))
        for wrapped in _wrap(c, label.supplier, "Helvetica", 8, text_w)[:2]:
            c.drawString(inner, y, wrapped)
            y -= 4 * mm
        c.setFillColor(black)

    # ── QR to the product record ────────────────────────────────────────────
    target = f"{settings.SAFETY_WEB_URL.rstrip('/')}/chemicals/{chemical_id}"
    widget = qr.QrCodeWidget(target)
    bounds = widget.getBounds()
    side = 26 * mm
    drawing = Drawing(side, side, transform=[
        side / (bounds[2] - bounds[0]), 0, 0, side / (bounds[3] - bounds[1]),
        0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, left + LABEL_W - 32 * mm, top - 38 * mm)
    c.setFont("Helvetica", 6)
    c.drawCentredString(left + LABEL_W - 32 * mm + side / 2, top - 42 * mm,
                        "Scan for the data sheet")

    # ── Footer ──────────────────────────────────────────────────────────────
    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillColor(HexColor("#666666"))
    footer = (f"Approved {label.approved_at:%Y-%m-%d} by "
              f"{label.approved_by_name or 'HSE'} · "
              "a safety data sheet for this product is available — scan the code")
    c.drawString(inner, top - LABEL_H + 4 * mm, footer[:150])
    c.setFillColor(black)


def render(label, *, chemical_id: str, copies: int = 2,
           artwork: dict[str, bytes] | None = None) -> bytes:
    """Two labels to an A4 sheet, laid out for a laminator."""
    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=A4)
    width, height = A4  # noqa: F841 - width unused, kept for readability

    per_page = 2
    for index in range(max(1, copies)):
        slot = index % per_page
        if slot == 0 and index:
            c.showPage()
        top = height - 10 * mm - slot * (LABEL_H + 8 * mm)
        _one_label(c, label, chemical_id, top, artwork)

    c.showPage()
    c.save()
    return buffer.getvalue()
