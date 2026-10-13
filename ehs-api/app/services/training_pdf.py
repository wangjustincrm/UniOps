"""The training register, printed.

Round 4, TC-10: "create a way to print training records based on the filter
created." The register answers three questions — who has done this course, what
has this person done, what happened in March — and the answer was on screen
only, so producing it for an auditor or a manager meant a screenshot.

★ The filter is printed on it. A page of training records with no statement of
what was asked for is not evidence of anything: "these twelve people are
trained" is a different claim from "these twelve of the four hundred records
match a search for 'forklift' in March". Every narrowing is named at the top,
and a print of everything says so too.

★ Every string is drawn in a face chosen for its own content, for the reason
`pdf_fonts` explains at length: Helvetica draws a Chinese name as a row of black
boxes, with no error raised. Plenty of people here have one.

Landscape, because five columns of names and course titles do not fit portrait
without truncating the two that identify the row.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timezone

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from app.services import pdf_fonts

INK = HexColor("#1f2937")
MUTED = HexColor("#6b7280")
RULE = HexColor("#c8cdd4")
ZEBRA = HexColor("#f4f5f7")

PAGE_W, PAGE_H = landscape(A4)
MARGIN = 14 * mm
# Person · Course · Completed · Expires · How · Entered by. Widths are in mm and
# add up to the printable width; the two that identify a row get the room.
# ★ Round 5, TC-08: "report has a heading called HOW and the data shows here.
# remove how and add the run by name and the instructors name."
#
# `How` was internal-or-external, which is true and is not what anybody reading
# a training record wants to know. Who ran the session and who taught it are,
# and both come from the session the record points at — a record entered by hand
# has no session and prints a dash, which is the honest answer rather than a
# blank that reads as missing data.
COLUMNS: tuple[tuple[str, float], ...] = (
    ("Person", 44),
    ("Course", 54),
    ("Completed", 23),
    ("Expires", 23),
    ("Length", 18),
    ("Run by", 38),
    ("Instructor", 38),
    ("Entered by", 31),
)


def length_text(minutes: int | None) -> str | None:
    """1 h 30 min, 45 min, 2 h. None for a session with no start or end."""
    if not minutes:
        return None
    h, m = divmod(minutes, 60)
    return " ".join(p for p in (f"{h} h" if h else "", f"{m} min" if m else "") if p)
ROW_H = 6.2 * mm
# ★ Set once, with the size. ReportLab's leading defaults to 12 whatever the
# font size is, and a row height chosen without saying so overlaps.
BODY_SIZE = 8.5


def _clip(c: pdfcanvas.Canvas, text: str, width: float, size: float,
          *, bold: bool = False) -> str:
    """As much of the string as fits, with an ellipsis where it does not.

    A name cut off mid-word is wrong but readable; one drawn over the next
    column is neither.
    """
    font = pdf_fonts.font_for(text, bold=bold)
    if c.stringWidth(text, font, size) <= width:
        return text
    ell = "…"
    out = text
    while out and c.stringWidth(out + ell, font, size) > width:
        out = out[:-1]
    return (out + ell) if out else ell


def _cell(c: pdfcanvas.Canvas, text: str, x: float, y: float, width: float,
          *, size: float = BODY_SIZE, bold: bool = False, colour=INK) -> None:
    font = pdf_fonts.font_for(text, bold=bold)
    c.setFont(font, size)
    c.setFillColor(colour)
    c.drawString(x, y, _clip(c, text, width, size, bold=bold))


def _header(c: pdfcanvas.Canvas, *, title: str, filters: list[str],
            count: int, printed_by: str | None, page: int) -> float:
    """The heading block, returning the y the table starts at."""
    y = PAGE_H - MARGIN
    c.setFont("Helvetica-Bold", 13)
    c.setFillColor(INK)
    c.drawString(MARGIN, y - 4 * mm, title)

    y -= 10 * mm
    c.setFont("Helvetica", 8.5)
    c.setFillColor(MUTED)
    # ★ What was asked for, in words, before the rows. See the module docstring.
    line = filters[0] if filters else "Every record on file, unfiltered."
    c.drawString(MARGIN, y, line)
    for extra in filters[1:]:
        y -= 4.4 * mm
        c.drawString(MARGIN, y, extra)

    y -= 4.8 * mm
    stamp = datetime.now(timezone.utc).astimezone().strftime("%d %b %Y, %H:%M")
    tail = f"{count} record{'' if count == 1 else 's'} · printed {stamp}"
    if printed_by:
        c.setFont(pdf_fonts.font_for(printed_by), 8.5)
        tail = f"{tail} · by {printed_by}"
    c.drawString(MARGIN, y, tail)
    c.setFont("Helvetica", 8)
    c.drawRightString(PAGE_W - MARGIN, y, f"Page {page}")

    y -= 6 * mm
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)

    y -= 5 * mm
    x = MARGIN
    for label, w in COLUMNS:
        c.setFont("Helvetica-Bold", 7.5)
        c.setFillColor(MUTED)
        c.drawString(x, y, label.upper())
        x += w * mm
    y -= 2.4 * mm
    c.setStrokeColor(RULE)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    return y - ROW_H


def render(
    *,
    rows: list[dict],
    filters: list[str],
    printed_by: str | None,
    title: str = "Training records",
) -> bytes:
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=landscape(A4))
    c.setTitle(title)

    page = 1
    y = _header(c, title=title, filters=filters, count=len(rows),
                printed_by=printed_by, page=page)

    if not rows:
        c.setFont("Helvetica", 9)
        c.setFillColor(MUTED)
        c.drawString(MARGIN, y, "Nothing matches that filter.")

    for i, r in enumerate(rows):
        if y < MARGIN + 8 * mm:
            c.showPage()
            page += 1
            y = _header(c, title=title, filters=filters, count=len(rows),
                        printed_by=printed_by, page=page)
        if i % 2 == 1:
            c.setFillColor(ZEBRA)
            c.rect(MARGIN - 1.5 * mm, y - 1.8 * mm,
                   PAGE_W - 2 * MARGIN + 3 * mm, ROW_H, stroke=0, fill=1)
        x = MARGIN
        values = (
            r["user_name"],
            r["course_label"],
            r["completed_on"],
            r["expires_on"],
            length_text(r.get("length_minutes")),
            r["run_by_name"],
            r["instructor_name"],
            r["recorded_by_name"],
        )
        for (label, w), value in zip(COLUMNS, values, strict=True):
            _cell(c, value or "—", x, y, w * mm - 3 * mm,
                  bold=(label == "Person"))
            x += w * mm
        y -= ROW_H

    c.showPage()
    c.save()
    return buf.getvalue()


def describe(
    *, search: str | None, completed_from: date | None, completed_to: date | None,
    person: str | None, course: str | None,
) -> list[str]:
    """The filter, in the words the screen uses for it.

    Kept next to the renderer because the print is only evidence if this says
    the same thing the list was narrowed by.
    """
    out: list[str] = []
    if person:
        out.append(f"Person: {person}")
    if course:
        out.append(f"Course: {course}")
    if search:
        out.append(f"Search: “{search}”")
    if completed_from and completed_to:
        out.append(f"Completed between {completed_from.isoformat()} "
                   f"and {completed_to.isoformat()}")
    elif completed_from:
        out.append(f"Completed on or after {completed_from.isoformat()}")
    elif completed_to:
        out.append(f"Completed on or before {completed_to.isoformat()}")
    return out
