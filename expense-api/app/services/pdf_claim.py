"""Approved-claim PDF for EXP / MIL / TRV / CFM (ReportLab).

The counterpart of epms-api's approved-PR PDF: once a claim is approved, this
document is attached to it, and it carries the approval trail — who submitted,
who approved each step, when, and what they wrote — so the claim can be filed
or handed to an auditor without anyone opening OA.

TRA keeps its own form (pdf_tra.py): it is a travel *application*, laid out as
the paper form it replaced, not a claim.

Pure: everything that needs the DB (approval trail, form labels, company name)
is resolved by the caller (services/claim_pdf.py) and passed in.
"""
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_PRIMARY = colors.HexColor("#0A7C7C")
_LIGHT = colors.HexColor("#F5F5F5")
_GRAY = colors.HexColor("#737373")
_DARK = colors.HexColor("#1A1A1A")

TYPE_TITLES = {
    "EXP": "General Expense Claim",
    "MIL": "Mileage Claim",
    "TRV": "Travel Expense Claim",
}

# ── Chinese text ──────────────────────────────────────────────────────────────
# Helvetica has no Chinese glyphs: a name like 王小明 renders as black boxes,
# silently. Chinese runs are switched to an EMBEDDED TrueType font (the reader's
# machine needs nothing installed); the English around them keeps Helvetica and
# its bold. Same approach as finance-api's bank_recon_report.py.
_CJK_RE = re.compile(
    "[⺀-㏿㐀-䶿一-鿿豈-﫿"
    "︰-﹏＀-￯]+"
)
_CJK_TTF_CANDIDATES = (
    ("wqy-zenhei", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("wqy-microhei", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ("droid-fallback", "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0),
)
_cjk_font_name: str | None = None


def _cjk_font() -> str:
    """A registered font that can draw Chinese: embedded TTF → CID reference →
    Helvetica. Resolved once; never raises."""
    global _cjk_font_name
    if _cjk_font_name is not None:
        return _cjk_font_name
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    for name, path, index in _CJK_TTF_CANDIDATES:
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=index))
            _cjk_font_name = name
            return name
        except Exception:
            continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _cjk_font_name = "STSong-Light"
    except Exception:
        _cjk_font_name = "Helvetica"
    return _cjk_font_name


def _rich(text) -> str:
    """Paragraph markup for user data: XML-escaped FIRST (Paragraph parses
    mini-XML, so an unescaped "&" swallows what follows), then Chinese runs
    wrapped in the CJK font. Newlines become line breaks."""
    t = escape(str(text if text is not None else ""))
    if _CJK_RE.search(t):
        font = _cjk_font()
        if font != "Helvetica":
            t = _CJK_RE.sub(lambda m: f'<font name="{font}">{m.group(0)}</font>', t)
    return t.replace("\n", "<br/>")


def _s(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


def _money(v) -> str:
    return f"{Decimal(str(v or 0)):,.2f}"


def _rate(v) -> str:
    """Rate per km: the column holds four decimals; show at least two."""
    if v is None:
        return ""
    whole, _, frac = f"{Decimal(str(v)):,.4f}".partition(".")
    return f"{whole}.{frac.rstrip('0').ljust(2, '0')}"


def _d(v) -> str:
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    return ""


def _dt_utc(v) -> str:
    """Approval timestamps with the time, in UTC and labelled as such — a bare
    date is ambiguous here (Toronto is UTC-4/5, so an evening approval lands on
    the next UTC day)."""
    if not isinstance(v, datetime):
        return ""
    if v.tzinfo is not None:
        v = v.astimezone(timezone.utc)
    return v.strftime("%Y-%m-%d %H:%M UTC")


def cfm_answers(notes: str | None) -> tuple[dict, str | None]:
    """CFM stores its field answers as a JSON blob in `notes`, with the free-text
    remark under `_user_notes` (oa CfmCreatePage). Returns (answers, remark).
    Notes that are not that JSON — hand-edited, or older — are all remark."""
    if not notes:
        return {}, None
    try:
        parsed = json.loads(notes)
    except (ValueError, TypeError):
        return {}, notes
    if not isinstance(parsed, dict):
        return {}, notes
    remark = parsed.pop("_user_notes", None)
    return parsed, (remark if isinstance(remark, str) and remark.strip() else None)


def build_claim_pdf(
    claim,
    approvals: list[dict] | None = None,
    *,
    company_name: str | None = None,
    form_name: str | None = None,
    cfm_fields: list[tuple[str, str]] | None = None,
    travel_application_number: str | None = None,
) -> bytes:
    """Render an approved claim.

    `approvals` rows are {"step", "name", "at", "comment"} in display order —
    the "Submitted" row first, then one per workflow step (see
    services/claim_pdf.approval_trail). `cfm_fields` is [(label, value)] for a
    custom form, already resolved against its field_schema.
    """
    buf = BytesIO()
    W = A4[0] - 40 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=claim.claim_number)

    # leading set explicitly everywhere: ReportLab's default is 12 regardless
    # of fontSize, and a 15pt line in a 12pt box overprints the next one.
    co_style = _s("co", fontSize=15, leading=18, textColor=_DARK, fontName="Helvetica-Bold")
    sub_style = _s("sub", fontSize=10, leading=13, textColor=_GRAY, fontName="Helvetica")
    num_style = _s("num", fontSize=14, leading=17, textColor=_PRIMARY, fontName="Helvetica-Bold", alignment=2)
    tag_style = _s("tag", fontSize=8, leading=10, textColor=_PRIMARY, fontName="Helvetica-Bold", alignment=2)
    lbl_style = _s("lbl", fontSize=8, leading=10, textColor=_GRAY, fontName="Helvetica")
    val_style = _s("val", fontSize=9, leading=12, textColor=_DARK, fontName="Helvetica")
    sec_style = _s("sec", fontSize=10, leading=13, textColor=_PRIMARY, fontName="Helvetica-Bold", spaceAfter=4)
    th_style = _s("th", fontSize=8, leading=10, textColor=colors.white, fontName="Helvetica-Bold")
    td_style = _s("td", fontSize=8, leading=10, textColor=_DARK, fontName="Helvetica")
    td_r = _s("tdr", parent=td_style, alignment=2)
    th_r = _s("thr", parent=th_style, alignment=2)
    footer_style = _s("ft", fontSize=7, leading=9, textColor=_GRAY, fontName="Helvetica")

    ct = claim.claim_type or ""
    is_cfm = ct.upper().startswith("CFM")
    title = (form_name or "Custom Form") if is_cfm else TYPE_TITLES.get(ct, ct)
    status_label = "Paid" if claim.status == "paid" else "Approved"

    els: list = []

    # ── Header ───────────────────────────────────────────────────────────────
    left = [Paragraph(_rich(company_name or "Expense Claim"), co_style), Paragraph(_rich(title), sub_style)]
    right = [Paragraph(_rich(claim.claim_number), num_style), Paragraph(status_label.upper(), tag_style)]
    header = Table([[left, right]], colWidths=[W * 0.6, W * 0.4])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    els += [header, HRFlowable(width="100%", thickness=2, color=_PRIMARY, spaceAfter=6)]

    # ── Meta grid ────────────────────────────────────────────────────────────
    def cell(lbl, val):
        return [Paragraph(lbl, lbl_style), Paragraph(_rich(val) if val not in (None, "") else "—", val_style)]

    pairs: list[tuple[str, str | None]] = [
        ("Claim No.", claim.claim_number), ("Claim Date", _d(claim.submission_date)),
        ("Employee", claim.employee_name), ("Department", claim.department_name),
        ("Currency", claim.currency), ("Submitted", _d(claim.submitted_at)),
    ]
    if ct == "TRV":
        dates = ""
        if claim.travel_from_date or claim.travel_to_date:
            dates = f"{_d(claim.travel_from_date)} – {_d(claim.travel_to_date)}"
        pairs += [("Destination", claim.travel_destination), ("Travel Dates", dates)]
        if travel_application_number:
            pairs += [("Travel Application", travel_application_number), ("", None)]
    if ct == "MIL":
        owned = {"self": "Personal", "company": "Company"}.get(claim.vehicle_owned_by or "", claim.vehicle_owned_by)
        pairs += [("Vehicle", claim.vehicle_description), ("Vehicle Owner", owned)]
    if claim.paid_at:
        pairs += [("Approved", _d(claim.approved_at)), ("Paid", _d(claim.paid_at))]
    elif claim.approved_at:
        pairs += [("Approved", _d(claim.approved_at)), ("", None)]

    rows = []
    for i in range(0, len(pairs), 2):
        a, b = pairs[i], pairs[i + 1] if i + 1 < len(pairs) else ("", None)
        rows.append([*cell(*a), *(cell(*b) if b[0] else ["", ""])])
    if claim.purpose:
        rows.append([Paragraph("Purpose", lbl_style), Paragraph(_rich(claim.purpose), val_style), "", ""])
    meta = Table(rows, colWidths=[W * 0.15, W * 0.35, W * 0.15, W * 0.35])
    meta_style = [
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, _LIGHT]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if claim.purpose:
        meta_style.append(("SPAN", (1, len(rows) - 1), (3, len(rows) - 1)))
    meta.setStyle(TableStyle(meta_style))
    els += [meta, Spacer(1, 5 * mm)]

    def grid(data, widths):
        t = Table(data, colWidths=widths, repeatRows=1)
        st = [
            ("BACKGROUND", (0, 0), (-1, 0), _PRIMARY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT]),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        t.setStyle(TableStyle(st))
        return t

    # ── Custom form answers ──────────────────────────────────────────────────
    remark = claim.notes
    if is_cfm:
        _, remark = cfm_answers(claim.notes)
        if cfm_fields:
            els.append(Paragraph("Form Details", sec_style))
            data = [[Paragraph("Field", th_style), Paragraph("Value", th_style)]]
            for label, value in cfm_fields:
                data.append([Paragraph(_rich(label), td_style),
                             Paragraph(_rich(value) if value not in (None, "") else "—", td_style)])
            els += [grid(data, [W * 0.30, W * 0.70]), Spacer(1, 5 * mm)]

    # ── Lines ────────────────────────────────────────────────────────────────
    if ct == "MIL":
        els.append(Paragraph("Trip Log", sec_style))
        hdr = ["#", "Date", "Route", "Purpose", "km", "Rate/km", "Amount"]
        data = [[Paragraph(h, th_r if i >= 4 else th_style) for i, h in enumerate(hdr)]]
        for t in claim.trip_items or []:
            # Not "→": Helvetica has no arrow glyph (it prints a box).
            route = f"{t.from_location} – {t.to_location}" + (" (round trip)" if t.is_round_trip else "")
            data.append([
                Paragraph(str(t.trip_number), td_style), Paragraph(_d(t.trip_date), td_style),
                Paragraph(_rich(route), td_style), Paragraph(_rich(t.purpose), td_style),
                Paragraph(f"{Decimal(str(t.distance_km)):,.2f}", td_r),
                Paragraph(_rate(t.rate_per_km), td_r),
                Paragraph(_money(t.amount), td_r),
            ])
        els.append(grid(data, [W * f for f in (0.05, 0.11, 0.28, 0.24, 0.09, 0.10, 0.13)]))
    elif claim.line_items:
        els.append(Paragraph("Line Items", sec_style))
        hdr = ["#", "Date", "Description", "Budget Account", "Cost Center", "Net", "Tax", "Total"]
        data = [[Paragraph(h, th_r if i >= 5 else th_style) for i, h in enumerate(hdr)]]
        for li in claim.line_items:
            acct = f"{li.budget_account_code} — {li.budget_account_name}" if li.budget_account_name else li.budget_account_code
            data.append([
                Paragraph(str(li.line_number), td_style), Paragraph(_d(li.expense_date), td_style),
                Paragraph(_rich(li.description), td_style), Paragraph(_rich(acct), td_style),
                Paragraph(_rich(li.cost_center_name or "—"), td_style),
                Paragraph(_money(li.net_amount), td_r), Paragraph(_money(li.tax_amount), td_r),
                Paragraph(_money(li.total_amount), td_r),
            ])
        els.append(grid(data, [W * f for f in (0.04, 0.11, 0.24, 0.21, 0.12, 0.09, 0.09, 0.10)]))

    # ── Totals ───────────────────────────────────────────────────────────────
    tl = _s("tl", fontSize=9, leading=12, fontName="Helvetica", alignment=2, textColor=_GRAY)
    tb = _s("tb", fontSize=10, leading=13, fontName="Helvetica-Bold", alignment=2)
    tv = _s("tv", fontSize=12, leading=15, textColor=_PRIMARY, fontName="Helvetica-Bold", alignment=2)
    tot_rows = []
    if ct == "MIL" and claim.total_km is not None:
        tot_rows.append([Paragraph("Total Distance", tl), Paragraph(f"{Decimal(str(claim.total_km)):,.2f} km", tl)])
    if Decimal(str(claim.tax_amount or 0)) != 0:
        tot_rows.append([Paragraph("Net", tl), Paragraph(_money(claim.net_amount), tl)])
        tot_rows.append([Paragraph("Tax", tl), Paragraph(_money(claim.tax_amount), tl)])
    tot_rows.append([Paragraph("Total (Reimbursable)", tb),
                     Paragraph(f"{escape(claim.currency or '')} {_money(claim.total_amount)}", tv)])
    totals = Table(tot_rows, colWidths=[W * 0.72, W * 0.28])
    totals.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEABOVE", (0, 0), (-1, 0), 1, _PRIMARY),
    ]))
    els += [totals]

    # ── Approvals ────────────────────────────────────────────────────────────
    els += [Spacer(1, 6 * mm), Paragraph("Approval Record", sec_style)]
    data = [[Paragraph(h, th_style) for h in ("Step", "By", "Date", "Comment")]]
    for a in approvals or []:
        data.append([
            Paragraph(_rich(a.get("step") or "—"), td_style),
            Paragraph(_rich(a.get("name") or "—"), td_style),
            Paragraph(_dt_utc(a.get("at")) or "—", td_style),
            Paragraph(_rich(a.get("comment") or ""), td_style),
        ])
    if len(data) == 1:
        data.append([Paragraph("—", td_style), Paragraph("No approval events recorded", td_style), "", ""])
    els.append(grid(data, [W * 0.22, W * 0.22, W * 0.20, W * 0.36]))

    # ── Notes ────────────────────────────────────────────────────────────────
    if remark and remark.strip():
        els += [Spacer(1, 5 * mm), Paragraph("Notes", sec_style), Paragraph(_rich(remark.strip()), val_style)]

    # ── Footer ───────────────────────────────────────────────────────────────
    els += [
        Spacer(1, 8 * mm), HRFlowable(width="100%", thickness=0.5, color=_GRAY),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            f" · Status: {status_label} · {escape(claim.claim_number)}",
            footer_style,
        ),
    ]
    doc.build(els)
    return buf.getvalue()
