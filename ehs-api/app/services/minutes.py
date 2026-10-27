"""A committee meeting's minutes, read and printed as one document.

Round 6, TC-04: "there is a disconnect between the meeting minutes and the
agenda. a saved meeting minutes form does not get saved on the meeting and there
is no means to print the meeting minutes including the attendance, agenda,
minutes, and recommendations."

The minutes *were* attached — the meeting carries the form's id and the form
names the meeting. What was missing is that nothing read them back: the meeting
showed only its own free-text box, which is empty when the minutes are written on
a form, so it said "No minutes recorded" over a filled-in, submitted form. So the
answers are read here, in the form's own order, and both the meeting page and
the printed minutes use the same rows.
"""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.form import FormAnswer, FormSubmission
from app.services import pdf_fonts, statutory


@dataclass
class Row:
    label: str
    text: str | None
    # A section heading on the form rather than a question.
    heading: bool = False
    # ★ Round 9: a question the form builder marked to stand out — "Bold and
    # increase the font size by 2" — printed two points larger, in bold.
    emphasis: bool = False


def _text(answer: FormAnswer, question: dict) -> str | None:
    qtype = answer.question_type
    if qtype == "photo":
        n = len(answer.value_ids or [])
        return f"{n} photograph{'s' if n != 1 else ''}" if n else None
    if qtype == "signature":
        # The value is the drawing itself; a document says that it was signed.
        return "Signed" if answer.value_text else None
    if answer.value_labels:
        return ", ".join(str(x) for x in answer.value_labels)
    if qtype == "yes_no_na" and answer.value_text:
        return {"yes": "Yes", "no": "No", "na": "N/A"}.get(answer.value_text.lower(),
                                                         answer.value_text)
    if qtype == "select" and answer.value_text:
        labels = {str(o.get("key")): o.get("label") for o in question.get("options") or []}
        return labels.get(answer.value_text, answer.value_text)
    if answer.value_text:
        return answer.value_text
    if answer.value_number is not None:
        n = answer.value_number
        return str(int(n)) if n == int(n) else str(n)
    if answer.value_date is not None:
        return answer.value_date.strftime("%-d %b %Y")
    if answer.value_bool is not None:
        return "Yes" if answer.value_bool else "No"
    return None


async def rows_for(db: AsyncSession, submission_id: uuid.UUID | None) -> list[Row]:
    """The minutes form's questions and answers, in the form's order."""
    if submission_id is None:
        return []
    sub = await db.get(FormSubmission, submission_id)
    if sub is None:
        return []
    answers = {a.question_key: a for a in (await db.execute(
        select(FormAnswer).where(FormAnswer.submission_id == sub.id))).scalars()}
    out: list[Row] = []
    for q in sub.schema_snapshot or []:
        qtype = q.get("type")
        if qtype == "group":
            out.append(Row(q.get("label") or "", None, heading=True))
            continue
        if qtype == "instruction":
            continue
        a = answers.get(q.get("key"))
        out.append(Row(q.get("label") or q.get("key") or "", _text(a, q) if a else None,
                       emphasis=bool(q.get("emphasis"))))
    return out


# ── The printed minutes ─────────────────────────────────────────────────────

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")
RULE = colors.HexColor("#D1D5DB")


def _style(size: float, *, bold: bool = False, colour=INK, after: float = 2) -> ParagraphStyle:
    return ParagraphStyle(
        "s", fontName="Helvetica-Bold" if bold else "Helvetica", fontSize=size,
        leading=size * 1.3, textColor=colour, alignment=TA_LEFT, spaceAfter=after)


def _p(text: str | None, style: ParagraphStyle) -> Paragraph:
    """A paragraph, escaped, with line breaks kept and CJK set in a face that
    has the glyphs (a name in Chinese printed as black boxes otherwise)."""
    body = escape(text or "—").replace("\n", "<br/>")
    if pdf_fonts.has_cjk(text):
        style = ParagraphStyle("cjk", parent=style,
                               fontName=pdf_fonts.font_for(text, bold=style.fontName.endswith("Bold")))
    return Paragraph(body, style)


class _NumberedCanvas(pdfcanvas.Canvas):
    """Draws "Page 1 of 2" at the foot of every page.

    Round 7, TC-01: "add the page number to the bottom, page 1 of 2." The total
    is known only once the last page is laid out, so pages are kept and drawn
    at the end.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages: list[dict] = []

    def showPage(self):  # noqa: N802 — ReportLab's name
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self.setFont("Helvetica", 8)
            self.setFillColor(MUTED)
            self.drawRightString(LETTER[0] - 18 * mm, 9 * mm,
                                 f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def _three_columns(names: list[str], style) -> Table:
    """Names across three columns, filled row by row."""
    rows = [names[i:i + 3] for i in range(0, len(names), 3)]
    rows[-1] = rows[-1] + [""] * (3 - len(rows[-1]))
    # An empty cell is empty; `_p` would print a dash for it.
    t = Table([[_p(n, style) if n else "" for n in row] for row in rows], colWidths=[58 * mm] * 3)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _due(d) -> str:
    return d.astimezone(statutory.ONTARIO).strftime("%-d %b %Y")


def render(*, meeting, attendance, recommendations, rows: list[Row],
           printed_by: str | None, reply_due: dict | None = None,
           logo_data_url: str | None = None) -> bytes:
    """`reply_due`: recommendation id → its 21-day reply deadline, if any."""
    reply_due = reply_due or {}
    """Attendance, agenda, minutes and recommendations, in that order."""
    h1, h2 = _style(15, bold=True, after=4), _style(11, bold=True, after=3)
    body, small = _style(9.5), _style(8, colour=MUTED)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Minutes {meeting.meeting_no}")
    story: list = []
    # ★ Round 10, question 7: the company logo, centred at the top.
    from app.services.branding import fit, logo_reader, logo_stream
    logo = logo_reader(logo_data_url)
    if logo is not None:
        lw, lh = fit(logo, 60 * mm, 14 * mm)
        story.append(Image(logo_stream(logo_data_url), width=lw, height=lh, hAlign="CENTER"))
        story.append(Spacer(1, 3 * mm))

    title = meeting.title or "Joint Health and Safety Committee"
    story.append(_p(f"{title} — minutes", h1))
    when = meeting.held_on.strftime("%-d %B %Y") if isinstance(meeting.held_on, date) else ""
    times = " – ".join(t.astimezone(statutory.ONTARIO).strftime("%H:%M")
                       for t in (meeting.started_at, meeting.ended_at) if t)
    facts = [meeting.meeting_no, when, times, meeting.location_text,
             (f"Inspection: {meeting.inspection_department_name}"
              if meeting.inspection_department_name else None)]
    story.append(_p(" · ".join(f for f in facts if f), small))
    story.append(Spacer(1, 5 * mm))

    story.append(_p("Attendance", h2))
    if attendance:
        # ★ Round 10, TC-07: "remove the role and the absent/present columns.
        # create a 3 column view listing those who attended under the Present
        # heading. under that, place a 3 column list titled Absent, of those who
        # did not attend. their roles do not need to be listed." Regrets did not
        # attend either; they are under Absent, marked so.
        present = [a.user_name for a in attendance if a.attendance == "present"]
        absent = [a.user_name + (" (regrets)" if a.attendance == "regrets" else "")
                  for a in attendance if a.attendance != "present"]
        for heading, names in (("Present", present), ("Absent", absent)):
            story.append(_p(heading, _style(9.5, bold=True, after=1)))
            if names:
                story.append(_three_columns(names, body))
            else:
                story.append(_p("Nobody.", body))
            story.append(Spacer(1, 2 * mm))
    else:
        story.append(_p("Not recorded.", body))
    story.append(Spacer(1, 4 * mm))

    story.append(_p("Agenda", h2))
    story.append(_p(meeting.agenda or "Not recorded.", body))
    story.append(Spacer(1, 4 * mm))

    story.append(_p("Minutes", h2))
    if meeting.minutes:
        story.append(_p(meeting.minutes, body))
    if rows:
        for r in rows:
            if r.heading:
                story.append(Spacer(1, 2 * mm))
                story.append(_p(r.label, _style(10, bold=True)))
            elif r.emphasis:
                story.append(_p(r.label, _style(10.5, bold=True, after=0)))
                story.append(_p(r.text or "Not answered", _style(11.5, bold=True)))
            else:
                story.append(_p(r.label, _style(8.5, bold=True, colour=MUTED, after=0)))
                story.append(_p(r.text or "Not answered", body))
    if not meeting.minutes and not rows:
        story.append(_p("No minutes recorded.", body))
    story.append(Spacer(1, 4 * mm))

    story.append(_p("Recommendations", h2))
    if recommendations:
        for rec in recommendations:
            story.append(_p(f"{rec.seq}. {rec.recommendation}", body))
            # ★ Round 7, TC-01: "the recommendations section should also
            # include who it is assigned to and the due date." A recommendation
            # is answered by whoever it was sent to, within the OHSA 21 days
            # whose clock started when it was sent — the reply deadline.
            to = ", ".join(str(x.get("name") or "") for x in (rec.sent_to or []) if x.get("name"))
            deadline = reply_due.get(rec.id)
            bits = [f"Raised by {rec.raised_by_name}" if rec.raised_by_name else None,
                    (f"Sent to {to or 'the employer'} on {_due(rec.sent_at)}"
                     if rec.sent_at else "Not yet sent, so not yet assigned"),
                    (f"Reply due {_due(deadline.due_at)}" if deadline and not rec.response
                     else None),
                    f"Response: {rec.response}" if rec.response else
                    ("No response yet" if rec.sent_at else None)]
            story.append(_p(" · ".join(b for b in bits if b), small))
    else:
        story.append(_p("None.", body))

    printed = datetime.now(statutory.ONTARIO).strftime("%-d %b %Y %H:%M")
    story.append(Spacer(1, 6 * mm))
    story.append(_p(f"Printed {printed}" + (f" by {printed_by}" if printed_by else ""), small))
    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()
