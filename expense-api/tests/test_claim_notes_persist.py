"""What a requester writes on a claim must reach the claim detail page.

Prod EXP-20260929-0001: the explanation typed in the Submit dialog was stored
(approval-api, shared approval_events) but never shown — the detail endpoint
served only expense_approval_events. The create form's Notes field does
round-trip (first test); the submit comment is what went missing (second).
Payment is the one event written to expense_approval_events (finance-api), so
the merged history must keep it too (last test).
"""
import uuid

from tests.conftest import _client, _make_token


def _exp_body(**extra):
    return {
        "claim_type": "EXP",
        "submission_date": "2026-09-29",
        "currency": "CAD",
        "line_items": [{
            "line_number": 1,
            "expense_date": "2026-09-25",
            "description": "Max plan",
            "budget_account_id": str(uuid.uuid4()),
            "budget_account_code": "CRM00303",
            "budget_account_name": "Software Fees",
            "total_amount": "158.20",
            "tax_amount": "18.20",
            "net_amount": "140.00",
        }],
        "trip_items": [],
        **extra,
    }


async def test_notes_round_trip_on_create(db_session):
    token = _make_token("requester", user_id=str(uuid.uuid4()))
    async with _client(token) as c:
        r = await c.post("/api/v1/expenses", json=_exp_body(notes="Software Fee Claim"))
        assert r.status_code == 201, r.text
        assert r.json()["notes"] == "Software Fee Claim"
        got = await c.get(f"/api/v1/expenses/{r.json()['id']}")
    assert got.status_code == 200, got.text
    assert got.json()["notes"] == "Software Fee Claim"


async def test_submit_comment_reaches_the_claim_detail(db_session):
    """The comment typed in the Submit dialog is stored by approval-api in the
    shared approval_events table. The detail endpoint used to serve the
    expense_approval_events relationship instead — a table nothing writes — so
    that comment vanished from the claim (prod EXP-20260929-0001)."""
    from sqlalchemy import text

    from app.models.approval_event_mirror import ApprovalEventMirror

    uid = uuid.uuid4()
    await db_session.execute(text("DELETE FROM users"))
    await db_session.execute(
        text("INSERT INTO users (id, full_name) VALUES (:u, 'Justin Wang')"), {"u": uid})
    await db_session.commit()

    token = _make_token("requester", user_id=str(uid))
    async with _client(token) as c:
        r = await c.post("/api/v1/expenses", json=_exp_body())
        assert r.status_code == 201, r.text
        claim = r.json()
        db_session.add(ApprovalEventMirror(
            document_type="exp", document_id=uuid.UUID(claim["id"]),
            document_number=claim["claim_number"], step_idx=0, action="submit",
            actor_id=uid, actor_role="requester",
            comment="Claude Monthly Subscription for Developing.",
        ))
        await db_session.commit()
        got = await c.get(f"/api/v1/expenses/{claim['id']}")

    assert got.status_code == 200, got.text
    events = got.json()["approval_events"]
    assert [(e["action"], e["actor_name"], e["comment"]) for e in events] == [
        ("submit", "Justin Wang", "Claude Monthly Subscription for Developing."),
    ]
    await db_session.execute(text("DELETE FROM users"))
    await db_session.commit()


async def test_another_claims_events_do_not_leak_in(db_session):
    """Filtered by this claim's id — the positive test above would also pass
    if every event in the table were returned."""
    from app.models.approval_event_mirror import ApprovalEventMirror

    token = _make_token("requester", user_id=str(uuid.uuid4()))
    async with _client(token) as c:
        r = await c.post("/api/v1/expenses", json=_exp_body())
        assert r.status_code == 201, r.text
        db_session.add(ApprovalEventMirror(
            document_type="exp", document_id=uuid.uuid4(), document_number="EXP-OTHER",
            step_idx=0, action="submit", actor_id=uuid.uuid4(), actor_role="requester",
            comment="someone else's claim",
        ))
        await db_session.commit()
        got = await c.get(f"/api/v1/expenses/{r.json()['id']}")
    assert got.json()["approval_events"] == []


async def test_payment_row_survives_alongside_the_shared_history(db_session):
    """finance-api records payment in expense_approval_events, not in the
    shared table. Reading only the shared table would drop "Paid" from every
    paid claim; the two must be merged and ordered by time."""
    from datetime import datetime, timedelta, timezone

    from app.models.approval_event_mirror import ApprovalEventMirror
    from app.models.expense import ExpenseApprovalEvent

    uid = uuid.uuid4()
    token = _make_token("requester", user_id=str(uid))
    t0 = datetime.now(timezone.utc)
    async with _client(token) as c:
        r = await c.post("/api/v1/expenses", json=_exp_body())
        assert r.status_code == 201, r.text
        claim = r.json()
        cid = uuid.UUID(claim["id"])
        # Inserted out of order on purpose: pay first, submit second.
        db_session.add(ExpenseApprovalEvent(
            claim_id=cid, actor_id=uuid.uuid4(), actor_name="Payment Officer",
            action="pay", comment="EFT 1234", from_status="approved", to_status="paid",
            created_at=t0 + timedelta(days=2),
        ))
        db_session.add(ApprovalEventMirror(
            document_type="exp", document_id=cid, document_number=claim["claim_number"],
            step_idx=0, action="submit", actor_id=uid, actor_role="requester",
            comment="why", created_at=t0,
        ))
        await db_session.commit()
        got = await c.get(f"/api/v1/expenses/{claim['id']}")

    assert got.status_code == 200, got.text
    events = got.json()["approval_events"]
    assert [e["action"] for e in events] == ["submit", "pay"]
    pay = events[1]
    assert (pay["actor_name"], pay["comment"], pay["to_status"]) == (
        "Payment Officer", "EFT 1234", "paid")
