"""English-only Travel Application PDF (ReportLab). Labels and data in Helvetica;
Chinese runs in user data switch to the embedded TrueType font, exactly as the
claim PDF does (pdf_claim._rich).

It used to set every data value in the built-in CID font STSong-Light, which is
only REFERENCED, never embedded: Adobe Acrobat without the Asian font pack drew
the whole form as a blank page, while Chrome showed it (ISS-0214)."""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)

from app.core.plant_time import fmt_date
from app.services.pdf_claim import _rich

_LABEL = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#334155"))
_DATA = ParagraphStyle("data", fontName="Helvetica", fontSize=9, textColor=colors.black, leading=12)
_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, spaceAfter=2)
_MODES = [("airplane", "Airplane"), ("train", "Train"), ("ship", "Ship"),
          ("car", "Car"), ("accommodation", "Accommodation"), ("meal", "Meal"), ("other", "Other")]


def _row(label, value):
    return [Paragraph(label, _LABEL), Paragraph(_rich(value) if value else "—", _DATA)]


def build_travel_application_pdf(claim, approvals: list[dict] | None = None) -> bytes:
    """Render the TRA PDF.

    `approvals` is a DB-free list of dicts already resolved by the caller —
    typically `[{"step_idx": int, "actor_name": str | None, "acted_date": str | None}, ...]`,
    filtered to `action == "approve"` — so this function stays pure/testable and
    doesn't need to know about ApprovalEventMirror or any DB session. Missing or
    empty `approvals` renders blank "________" signature lines.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    els = [Paragraph("Application of Travel", _H1),
           Paragraph(f"No. {_rich(claim.claim_number)}", _DATA), Spacer(1, 8)]

    travelers = ", ".join(t.user_name for t in (claim.travelers or []))
    dates = ""
    if claim.travel_from_date and claim.travel_to_date:
        dates = f"{fmt_date(claim.travel_from_date)} to {fmt_date(claim.travel_to_date)}"
    leave = ""
    if claim.leave_from_date and claim.leave_to_date:
        leave = f"{fmt_date(claim.leave_from_date)} to {fmt_date(claim.leave_to_date)}"
    chosen = set(claim.transport_modes or [])
    transport = "  ".join(f"[{'X' if k in chosen else ' '}] {lbl}" for k, lbl in _MODES)

    info = Table([
        _row("Department", claim.department_name),
        _row("Time of Application", fmt_date(claim.submission_date) if claim.submission_date else ""),
        _row("Staff", travelers),
        _row("Number of Persons", str(len(claim.travelers or []))),
        _row("Location", claim.travel_destination),
        _row("Time", dates),
        _row("Reasons and Explanation", claim.purpose),
        _row("Period off", leave),
        _row("Transportation and Accommodation", transport),
        _row("Remarks", claim.notes),
    ], colWidths=[55 * mm, 119 * mm])
    info.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    els += [info, Spacer(1, 14)]

    # Signature rows from pre-resolved approval dicts (approve actions only), by step_idx.
    # step_idx is read defensively (dict.get) so a caller passing a malformed/partial
    # dict can't crash PDF build — it just falls through to a blank signature line.
    by_step = {a.get("step_idx"): a for a in (approvals or []) if a.get("step_idx") is not None}
    sig_rows = [[Paragraph("Approval", _LABEL), Paragraph("Approver", _LABEL), Paragraph("Date", _LABEL)]]
    for idx, title in enumerate(["Head of Department", "Finance", "General Manager"]):
        ev = by_step.get(idx)
        who = ev.get("actor_name") if ev else None
        when = ev.get("acted_date") if ev else None
        sig_rows.append([Paragraph(title, _DATA), Paragraph(_rich(who) if who else "________", _DATA),
                         Paragraph(_rich(when) if when else "________", _DATA)])
    sig = Table(sig_rows, colWidths=[58 * mm, 58 * mm, 58 * mm])
    sig.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    els.append(sig)
    doc.build(els)
    return buf.getvalue()
