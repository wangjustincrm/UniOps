"""Approved-claim PDF: generated when the chain finishes, regenerable, and it
carries the approval record.

Assertions are on the PDF bytes (page compression off so the content stream is
searchable), not on "the function returned something".
"""
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from reportlab import rl_config
from sqlalchemy import select, text

import app.db.base as db_module
from app.models.approval_event_mirror import ApprovalEventMirror as AEM
from app.models.company_config_mirror import EpmsCompanyConfig
from app.models.expense import ExpenseClaim, ExpenseLineItem, ExpenseTripItem
from app.services import claim_pdf
from app.services.pdf_claim import build_claim_pdf, cfm_answers
from tests.conftest import _client, _make_token


@pytest.fixture
def uncompressed(monkeypatch):
    monkeypatch.setattr(rl_config, "pageCompression", 0)


_TJ = re.compile(rb"\(((?:\\.|[^\\)])*)\) Tj")


def _text(pdf: bytes) -> str:
    """The printed text, one line per text object. ReportLab splits a line into
    several Tj strings (an escaped "&" is its own), so raw-byte substring
    search misses text that IS printed; join each line's strings first."""
    lines = []
    for raw in pdf.split(b"\n"):
        parts = _TJ.findall(raw)
        if parts:
            joined = b"".join(parts)
            joined = re.sub(rb"\\([()\\])", rb"\1", joined)
            lines.append(joined.decode("latin-1"))
    return "\n".join(lines)


def _exp(**kw) -> ExpenseClaim:
    fields = dict(
        id=uuid.uuid4(), claim_number="EXP-X-0001", claim_type="EXP",
        employee_id=uuid.uuid4(), employee_name="Jane Doe", department_name="Quality",
        submission_date=date(2026, 10, 1), currency="CAD", purpose="Client visit",
        total_amount=Decimal("113.00"), tax_amount=Decimal("13.00"), net_amount=Decimal("100.00"),
        status="approved", created_by=uuid.uuid4())
    c = ExpenseClaim(**{**fields, **kw})
    c.line_items = [ExpenseLineItem(
        line_number=1, expense_date=date(2026, 9, 30), description="Parking & tolls",
        budget_account_id=uuid.uuid4(), budget_account_code="CRM00901",
        budget_account_name="Travel", total_amount=Decimal("113.00"),
        tax_amount=Decimal("13.00"), net_amount=Decimal("100.00"))]
    c.trip_items = []
    return c


# ── Renderer ──────────────────────────────────────────────────────────────────

def test_pdf_carries_the_approval_record(uncompressed):
    at = datetime(2026, 10, 2, 14, 5, tzinfo=timezone.utc)
    data = build_claim_pdf(_exp(), [
        {"step": "Submitted", "name": "Jane Doe", "at": at, "comment": "for the audit"},
        {"step": "Department Head", "name": "Mark Boss", "at": at, "comment": "OK"},
        {"step": "Finance BP", "name": None, "at": None, "comment": "Skipped - no approval required"},
    ], company_name="Canada Royal Milk")
    assert data[:4] == b"%PDF"
    txt = _text(data)
    for s in ("Approval Record", "Department Head", "Mark Boss", "2026-10-02 14:05 UTC",
              "for the audit", "Finance BP", "Canada Royal Milk", "General Expense Claim",
              "EXP-X-0001", "CRM00901"):
        assert s in txt, s


def test_ampersand_in_user_text_survives(uncompressed):
    """Paragraph parses mini-XML: unescaped, "Food&Beverage" prints as "Food &Beverage;"."""
    c = _exp()
    c.line_items[0].description = "Food&Beverage"
    txt = _text(build_claim_pdf(c, []))
    assert "Food&Beverage" in txt and "Food &Beverage;" not in txt


def test_chinese_name_is_drawn_in_a_cjk_font_not_helvetica():
    data = build_claim_pdf(_exp(employee_name="王小明"), [])
    # Helvetica has no Chinese glyphs; the run must have been switched to a
    # font that does (embedded wqy in the image, CID fallback elsewhere).
    assert b"WenQuanYi" in data or b"STSong" in data or b"Droid" in data


def test_mileage_claim_prints_the_trip_log(uncompressed):
    c = _exp()
    c.claim_type, c.line_items = "MIL", []
    c.total_km = Decimal("42.00")
    c.trip_items = [ExpenseTripItem(
        trip_number=1, trip_date=date(2026, 9, 29), from_location="Plant", to_location="Port",
        purpose="Pickup", is_round_trip=True, distance_km=Decimal("42.00"),
        rate_per_km=Decimal("0.7200"), amount=Decimal("30.24"))]
    txt = _text(build_claim_pdf(c, []))
    for s in ("Mileage Claim", "Trip Log", "Plant \\226 Port (round trip)", "0.72", "42.00 km"):
        assert s in txt, s


def test_custom_form_answers_are_unpacked_from_notes():
    answers, remark = cfm_answers('{"po": "123", "_user_notes": "see attached"}')
    assert answers == {"po": "123"} and remark == "see attached"
    assert cfm_answers("plain text") == ({}, "plain text")


def test_custom_form_prints_labels_and_remark(uncompressed):
    c = _exp(notes='{"vendor": "ACME", "_user_notes": "urgent"}')
    c.claim_type, c.line_items = "CFM_GIFT", []
    data = build_claim_pdf(c, [], form_name="Gift Request",
                           cfm_fields=[("Vendor Name", "ACME")])
    txt = _text(data)
    for s in ("Gift Request", "Form Details", "Vendor Name", "ACME", "urgent"):
        assert s in txt, s
    assert "_user_notes" not in txt


# ── Approval trail ────────────────────────────────────────────────────────────

@pytest.fixture
async def exp_workflow():
    """workflow_defs["exp"] for the duration of the test, restored after —
    the company_config row is shared with the rest of the suite."""
    async with db_module.AsyncSessionLocal() as db:
        cfg = (await db.execute(select(EpmsCompanyConfig))).scalars().first()
        if cfg is None:
            cfg = EpmsCompanyConfig(id=uuid.uuid4(), dept_gm_opm_mapping={}, workflow_defs={},
                                    role_management={})
            db.add(cfg)
        original = dict(cfg.workflow_defs or {})
        cfg.workflow_defs = {**original, "exp": [
            {"role": "dept_head", "label": "Department Head"},
            {"role": "finance_bp", "label": "Finance BP"},
        ]}
        await db.commit()
        cfg_id = cfg.id
    yield
    async with db_module.AsyncSessionLocal() as db:
        cfg = await db.get(EpmsCompanyConfig, cfg_id)
        cfg.workflow_defs = original
        await db.commit()


async def _persist(db, claim: ExpenseClaim) -> ExpenseClaim:
    claim.claim_number = f"EXP-PDF-{uuid.uuid4().hex[:8]}"
    db.add(claim)
    await db.commit()
    return claim


def _event(claim, action, step, actor, minutes, comment=None):
    return AEM(document_type="exp", document_id=claim.id, document_number=claim.claim_number,
               step_idx=step, action=action, actor_id=actor, actor_role="x", comment=comment,
               created_at=datetime(2026, 10, 1, 12, tzinfo=timezone.utc) + timedelta(minutes=minutes))


async def test_trail_counts_only_the_last_round_and_marks_skipped_steps(db_session, exp_workflow):
    claim = await _persist(db_session, _exp())
    jane, boss, old = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db_session.execute(text(
        "INSERT INTO users (id, full_name) VALUES (:a, 'Jane Doe'), (:b, 'Mark Boss'), (:c, 'Old Approver')"),
        {"a": jane, "b": boss, "c": old})
    db_session.add_all([
        _event(claim, "submit", 0, jane, 0, "first try"),
        _event(claim, "approve", 0, old, 1, "stale"),
        _event(claim, "return", 1, old, 2, "fix receipt"),
        _event(claim, "submit", 0, jane, 3, "fixed"),
        _event(claim, "approve", 0, boss, 4, "OK"),
    ])
    await db_session.commit()

    rows = await claim_pdf.approval_trail(db_session, claim)

    assert [(r["step"], r["name"], r["comment"]) for r in rows] == [
        ("Submitted", "Jane Doe", "fixed"),
        ("Department Head", "Mark Boss", "OK"),
        ("Finance BP", None, "Skipped — no approval required"),
    ]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_file_server(mocker):
    keys, deleted = [], []

    async def _upload(data, filename, mime, kind, doc_id, token):
        assert data[:4] == b"%PDF"
        keys.append(uuid.uuid4())
        return keys[-1]

    async def _delete(storage_key, token):
        deleted.append(storage_key)

    mocker.patch("app.services.attachment_helper.upload_to_file_server", side_effect=_upload)
    mocker.patch("app.services.attachment_helper.delete_from_file_server", side_effect=_delete)
    return {"keys": keys, "deleted": deleted}


async def _pdf_rows(db, claim):
    await db.rollback()
    return [r[0] for r in (await db.execute(text(
        "SELECT file_id FROM expense_attachments WHERE claim_id = :c AND file_name = :n"),
        {"c": str(claim.id), "n": f"{claim.claim_number}.pdf"})).all()]


async def test_regenerate_replaces_rather_than_piles_up(admin_client, db_session, fake_file_server):
    claim = await _persist(db_session, _exp())
    url = f"/api/v1/expenses/{claim.id}/regenerate-pdf"
    assert (await admin_client.post(url)).status_code == 200
    second = await admin_client.post(url)
    assert second.status_code == 200, second.text
    assert second.json()["replaced"] == 1
    assert await _pdf_rows(db_session, claim) == [str(fake_file_server["keys"][-1])]
    assert fake_file_server["deleted"] == [fake_file_server["keys"][0]]


async def test_regenerate_refused_before_approval(admin_client, db_session, fake_file_server):
    claim = await _persist(db_session, _exp(status="in_review"))
    r = await admin_client.post(f"/api/v1/expenses/{claim.id}/regenerate-pdf")
    assert r.status_code == 409
    assert fake_file_server["keys"] == []


async def test_regenerate_open_to_owner_closed_to_stranger(db_session, fake_file_server):
    owner = uuid.uuid4()
    claim = await _persist(db_session, _exp(employee_id=owner))
    url = f"/api/v1/expenses/{claim.id}/regenerate-pdf"
    async with _client(_make_token("requester", str(uuid.uuid4()))) as stranger:
        assert (await stranger.post(url)).status_code == 403
    async with _client(_make_token("requester", str(owner))) as me:
        assert (await me.post(url)).status_code == 200


async def _approve_via_engine(mocker, claim_id, *, final: bool):
    """approval-api stands in: it writes the new status straight to the shared DB."""
    async def _delegate(key, doc_id, action, comment, token):
        async with db_module.AsyncSessionLocal() as db:
            await db.execute(text("UPDATE expense_claims SET status = :s WHERE id = :i"),
                             {"s": "approved" if final else "in_review", "i": str(claim_id)})
            await db.commit()
        return {}
    mocker.patch("app.api.v1.expenses.delegate_action", side_effect=_delegate)


async def test_final_approval_attaches_the_pdf(admin_client, db_session, fake_file_server, mocker):
    claim = await _persist(db_session, _exp(status="in_review"))
    await _approve_via_engine(mocker, claim.id, final=True)

    r = await admin_client.post(f"/api/v1/expenses/{claim.id}/action", json={"action": "approve"})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert [a["file_name"] for a in r.json()["attachments"]] == [f"{claim.claim_number}.pdf"]
    assert len(await _pdf_rows(db_session, claim)) == 1


async def test_intermediate_approval_attaches_nothing(admin_client, db_session, fake_file_server, mocker):
    claim = await _persist(db_session, _exp(status="submitted"))
    await _approve_via_engine(mocker, claim.id, final=False)

    r = await admin_client.post(f"/api/v1/expenses/{claim.id}/action", json={"action": "approve"})

    assert r.status_code == 200, r.text
    assert fake_file_server["keys"] == []


async def test_pdf_failure_does_not_fail_the_approval(admin_client, db_session, mocker):
    claim = await _persist(db_session, _exp(status="in_review"))
    await _approve_via_engine(mocker, claim.id, final=True)
    mocker.patch("app.services.attachment_helper.upload_to_file_server",
                 side_effect=RuntimeError("file-api down"))

    r = await admin_client.post(f"/api/v1/expenses/{claim.id}/action", json={"action": "approve"})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert r.json()["attachments"] == []
