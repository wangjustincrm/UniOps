"""The certificate the plant issues for its own training.

A course taken outside arrives as a file somebody uploads. A course run here
produced nothing the person could keep, which is what the third acceptance
round asked for — and what somebody asks for the day they apply for a job
somewhere else.

★ The wording is the plant's, from Settings. What makes it evidence is not:
who earned it, which course, when, until when, and the record number it can be
checked against are always on it, in that order, and cannot be configured away.

★ Every string is drawn in a face chosen for its own content. ReportLab's
built-in faces carry no CJK glyphs, so a Chinese name — and plenty of people
here have one — comes out as a row of black boxes with no error raised. The
name is the whole point of a certificate. See `pdf_fonts`.
"""
from __future__ import annotations

import base64
import io
from datetime import date

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

from app.services import pdf_fonts

DEFAULT_HEADING = "Certificate of Training"
DEFAULT_BODY = "has completed the following training to the standard required."
DEFAULT_SIGNATORY = "Health, Safety and Environment"

INK = HexColor("#1f2937")
MUTED = HexColor("#6b7280")
RULE = HexColor("#c8cdd4")


def _centre(c, text: str, y: float, *, size: float, bold: bool = False,
            colour=INK) -> None:
    """One centred line, in whichever face can draw it."""
    width, _ = landscape(A4)
    c.setFont(pdf_fonts.font_for(text, bold=bold), size)
    c.setFillColor(colour)
    c.drawCentredString(width / 2, y, text)


def _logo(data_url: str | None):
    """See services/branding.py."""
    from app.services.branding import logo_reader
    return logo_reader(data_url)


LOGO_MAX_W = 70 * mm
LOGO_MAX_H = 20 * mm


def render(
    *,
    person_name: str,
    course_name: str,
    course_code: str | None,
    completed_on: date,
    expires_on: date | None,
    record_no: str,
    verify_url: str | None,
    heading: str | None = None,
    body: str | None = None,
    signatory: str | None = None,
    plant_name: str = "Canada Royal Milk",
    logo_data_url: str | None = None,
) -> bytes:
    """One certificate, landscape A4."""
    buffer = io.BytesIO()
    width, height = landscape(A4)
    c = pdfcanvas.Canvas(buffer, pagesize=landscape(A4))

    # A border, inset, so a printer's unprintable margin does not clip it.
    c.setStrokeColor(RULE)
    c.setLineWidth(1.2)
    c.rect(14 * mm, 14 * mm, width - 28 * mm, height - 28 * mm)

    # ★ Round 9: "In-house training certificates should have the CRM logo
    # centered on the top of the page, use the CRM logo found on the admin
    # panel, company settings." Scaled to fit 70 × 20 mm, keeping its shape;
    # everything below moves down to make room.
    top = height
    logo = _logo(logo_data_url)
    if logo is not None:
        w, h = logo.getSize()
        scale = min(LOGO_MAX_W / w, LOGO_MAX_H / h)
        lw, lh = w * scale, h * scale
        c.drawImage(logo, (width - lw) / 2, height - 20 * mm - lh, lw, lh, mask="auto")
        top = height - 14 * mm

    _centre(c, plant_name, top - 34 * mm, size=11, colour=MUTED)
    _centre(c, heading or DEFAULT_HEADING, top - 52 * mm, size=24, bold=True)

    _centre(c, "This is to certify that", top - 70 * mm, size=11, colour=MUTED)
    _centre(c, person_name, top - 87 * mm, size=20, bold=True)

    _centre(c, body or DEFAULT_BODY, top - 102 * mm, size=11, colour=MUTED)

    course_line = f"{course_name} ({course_code})" if course_code else course_name
    _centre(c, course_line, top - 119 * mm, size=15, bold=True)

    dated = f"Completed {completed_on:%d %B %Y}"
    if expires_on:
        dated += f"  ·  valid until {expires_on:%d %B %Y}"
    _centre(c, dated, top - 132 * mm, size=11)

    # ── The part that makes it checkable ────────────────────────────────────
    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.line(40 * mm, 46 * mm, 120 * mm, 46 * mm)
    signed_by = signatory or DEFAULT_SIGNATORY
    c.setFont(pdf_fonts.font_for(signed_by), 9)
    c.setFillColor(MUTED)
    c.drawString(40 * mm, 40 * mm, signed_by)

    c.setFont("Helvetica", 8)
    c.drawString(40 * mm, 30 * mm, f"Record {record_no}")
    c.drawString(40 * mm, 25 * mm,
                 "This certificate is issued from the training register and can be "
                 "checked against it.")

    if verify_url:
        code = qr.QrCodeWidget(verify_url)
        bounds = code.getBounds()
        size = 26 * mm
        drawing = Drawing(
            size, size,
            transform=[size / (bounds[2] - bounds[0]), 0, 0,
                       size / (bounds[3] - bounds[1]), 0, 0])
        drawing.add(code)
        renderPDF.draw(drawing, c, width - 66 * mm, 24 * mm)

    c.setFillColor(black)
    c.showPage()
    c.save()
    return buffer.getvalue()
