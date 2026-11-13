import uuid
from datetime import date
from app.models.expense import ExpenseClaim, ExpenseTraveler
from app.services.pdf_tra import build_travel_application_pdf


def _claim():
    claim = ExpenseClaim(
        id=uuid.uuid4(), claim_number="TRA-20260803-0001", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="Alice", department_name="Operations",
        submission_date=date(2026, 8, 3), travel_destination="Toronto",
        travel_from_date=date(2026, 8, 4), travel_to_date=date(2026, 8, 6),
        purpose="Supplier audit", notes="Driver Sam (external) accompanies",
        transport_modes=["airplane", "accommodation", "meal"],
        leave_from_date=date(2026, 8, 4), leave_to_date=date(2026, 8, 6),
        status="approved", created_by=uuid.uuid4())
    claim.travelers = [ExpenseTraveler(user_id=uuid.uuid4(), user_name="Alice", seq=0),
                       ExpenseTraveler(user_id=uuid.uuid4(), user_name="Bob", seq=1)]
    claim.approval_events = []
    return claim


def test_build_travel_application_pdf_returns_pdf_bytes():
    """No approvals passed → still a valid PDF with blank signature lines."""
    claim = _claim()
    data = build_travel_application_pdf(claim)
    assert isinstance(data, (bytes, bytearray))
    assert data[:4] == b"%PDF"
    assert len(data) > 800


def test_build_travel_application_pdf_populates_signatures_from_approvals():
    """Passing explicit `approvals` (the real, DB-resolved source — see task-8
    fix report) must exercise the populated signature-row path, not silently
    fall back to blank "________" lines.
    """
    claim = _claim()
    approvals = [
        {"step_idx": 0, "actor_name": "Alice Head", "acted_date": "2026-08-05"},
        {"step_idx": 1, "actor_name": "Finance Fred", "acted_date": "2026-08-06"},
        {"step_idx": 2, "actor_name": "GM Grace", "acted_date": "2026-08-07"},
    ]
    data = build_travel_application_pdf(claim, approvals=approvals)
    assert isinstance(data, (bytes, bytearray))
    assert data[:4] == b"%PDF"
    assert len(data) > 800

    # The populated build must differ from the blank-signature build (proves the
    # approvals path actually changes the rendered content instead of being dead).
    blank = build_travel_application_pdf(claim, approvals=None)
    assert data != blank


def test_build_travel_application_pdf_ignores_approval_missing_step_idx():
    """A malformed approval dict (no step_idx) must not crash PDF build — it's
    simply skipped, leaving that signature row blank (task-8 fix: fail-safe
    step_idx access)."""
    claim = _claim()
    data = build_travel_application_pdf(claim, approvals=[{"actor_name": "No Step"}])
    assert data[:4] == b"%PDF"
    assert len(data) > 800


# ── ISS-0214: blank page in Adobe Acrobat ────────────────────────────────────
# Every data value was set in STSong-Light, a CID font ReportLab only REFERENCES
# (no FontFile in the PDF). Acrobat without the Asian font pack drew nothing;
# Chrome substituted a system font. Data now uses Helvetica (one of the standard
# 14 every reader has) and only Chinese runs switch to the embedded TrueType
# font — the claim PDF's approach, which opens fine in Acrobat.

import re as _re

_BASEFONT = _re.compile(rb"/BaseFont\s*/([A-Za-z0-9+\-_]+)")
_STANDARD_14 = {b"Helvetica", b"Helvetica-Bold"}


def _chinese_claim():
    c = _claim()
    c.department_name = "运营部 Operations"
    c.purpose = "Supplier audit & 工厂审核 <Q4>"
    c.travelers[0].user_name = "王小明"
    return c


def test_tra_pdf_never_references_an_unembedded_cid_font():
    data = build_travel_application_pdf(
        _chinese_claim(),
        approvals=[{"step_idx": 0, "actor_name": "李经理", "acted_date": "2026-08-05"}])
    assert b"STSong" not in data
    assert b"UniGB-UCS2-H" not in data
    fonts = set(_BASEFONT.findall(data))
    # Anything that is not a standard-14 font must be the embedded subset
    # (ReportLab names TrueType subsets "AAAAAA+<font>") with its FontFile2.
    others = fonts - _STANDARD_14
    assert others, fonts                      # the Chinese did get a CJK font
    assert all(b"+" in f for f in others), fonts
    assert data.count(b"/FontFile2") >= len(others)


def test_tra_pdf_english_only_uses_standard_fonts_only():
    fonts = set(_BASEFONT.findall(build_travel_application_pdf(_claim())))
    assert fonts <= _STANDARD_14, fonts


def _content(pdf: bytes) -> bytes:
    """Content streams, decoded (ReportLab writes ASCII85 + Flate)."""
    import base64
    import zlib
    out = []
    for raw in _re.findall(rb"stream\r?\n(.*?)endstream", pdf, _re.S):
        raw = raw.strip()
        if raw.endswith(b"~>"):
            raw = base64.a85decode(raw[:-2])
        try:
            out.append(zlib.decompress(raw))
        except zlib.error:
            out.append(raw)
    return b"\n".join(out)


def test_tra_pdf_escapes_markup_in_user_text():
    """Paragraph parses mini-XML: "&" and "<Q4>" in user text must print as
    typed, not break the paragraph (they were passed through unescaped)."""
    text = _content(build_travel_application_pdf(_chinese_claim()))
    assert b"(Supplier audit & ) Tj" in text
    assert b"( <) Tj (Q4) Tj (>) Tj" in text
