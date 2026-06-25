"""POST /finance/v1/payments/execute — unified payment executor tests (Phase 0-B1.5)."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.mirrors import CompanyConfig, ExpenseClaim, Invoice, Task
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.models.posting import PostingEvent, PostingLine


def _token(role: str = "ap_clerk", user_id: str | None = None) -> str:
    payload = {
        "sub": user_id or str(uuid.uuid4()), "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _pa(status="approved", po_id=None, invoice_ids=None) -> PaymentApplication:
    return PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Test PA", pa_type="PA-DIR",
        status=status, po_id=po_id, po_number="PO-1" if po_id else None,
        vendor_id=uuid.uuid4(), vendor_name="ACME Inc",
        invoice_ids=invoice_ids or [], payment_amount=Decimal("500.00"),
        currency="CAD", created_by=uuid.uuid4(),
    )


def _claim(status="approved") -> ExpenseClaim:
    return ExpenseClaim(
        claim_number=f"EXP-{uuid.uuid4().hex[:8]}", claim_type="EXP", status=status,
        employee_name="Jane Doe", currency="CAD",
        total_amount=Decimal("113.00"), tax_amount=Decimal("13.00"),
        net_amount=Decimal("100.00"),
    )


async def _execute(client, doc_kind, doc_id, role="ap_clerk", user_id=None, **body):
    return await client.post(
        "/finance/v1/payments/execute",
        json={"doc_kind": doc_kind, "doc_id": str(doc_id), **body},
        headers={"Authorization": f"Bearer {_token(role, user_id)}"},
    )


async def test_execute_pa_dir_flips_status_records_payment_emits_event(client, db_session):
    pa = _pa()
    db_session.add(pa)
    await db_session.flush()
    task = Task(document_type="pa_dir", document_id=pa.id)
    db_session.add(task)
    await db_session.flush()

    r = await _execute(client, "pa_dir", pa.id,
                       payment_date="2026-06-11", reference="TXN-42", notes="wire ok")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_status"] == "processed"

    await db_session.refresh(pa)
    assert pa.status == "processed"

    rec = (await db_session.execute(
        select(PaymentRecord).where(PaymentRecord.doc_id == pa.id)
    )).scalar_one()
    assert rec.doc_kind == "pa_dir"
    assert rec.reference == "TXN-42"
    assert rec.amount == Decimal("500.00")

    await db_session.refresh(task)
    assert task.is_completed is True   # engine's removed process branch used to do this

    ev = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == pa.id)
    )).scalar_one()
    assert ev.event_type == "payment"
    assert ev.fiscal_period == datetime.now(timezone.utc).strftime("%Y-%m")  # B1.7 stamp
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id).order_by(PostingLine.line_no)
    )).scalars().all()
    assert [ln.line_role for ln in lines] == ["accounts_payable", "bank"]
    assert lines[0].debit == lines[1].credit == Decimal("500.00")


async def test_execute_blocked_when_period_closed(client, db_session):
    """FIN-GL-002 close gate: payments in a soft/hard-closed period are rejected."""
    from app.models.fiscal_period import FiscalPeriod
    today_period = datetime.now(timezone.utc).strftime("%Y-%m")
    db_session.add(FiscalPeriod(period=today_period, status="hard_closed"))
    pa = _pa()
    db_session.add(pa)
    await db_session.flush()

    r = await _execute(client, "pa_dir", pa.id)
    assert r.status_code == 409
    assert "closed" in r.json()["detail"]


async def test_execute_pa_po_marks_invoices_paid(client, db_session):
    from datetime import date as _date
    inv = Invoice(
        status="matched", internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
        vendor_invoice_number="VI-1", vendor_id=uuid.uuid4(), vendor_name="ACME",
        amount=Decimal("10.00"), tax_amount=Decimal("0"), total_amount=Decimal("10.00"),
        currency="CAD", invoice_date=_date(2026, 6, 1), due_date=_date(2026, 7, 1),
    )
    db_session.add(inv)
    await db_session.flush()
    pa = _pa(po_id=uuid.uuid4(), invoice_ids=[str(inv.id)])
    db_session.add(pa)
    await db_session.flush()

    r = await _execute(client, "pa", pa.id)
    assert r.status_code == 200, r.text
    await db_session.refresh(inv)
    assert inv.status == "paid"


async def test_execute_claim_flips_paid_and_emits_three_lines(client, db_session):
    claim = _claim()
    db_session.add(claim)
    await db_session.flush()

    r = await _execute(client, "expense_claim", claim.id)
    assert r.status_code == 200, r.text
    await db_session.refresh(claim)
    assert claim.status == "paid"
    assert claim.paid_at is not None

    ev = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == claim.id)
    )).scalar_one()
    assert ev.event_type == "expense_paid"
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id).order_by(PostingLine.line_no)
    )).scalars().all()
    assert [ln.line_role for ln in lines] == ["employee_expense", "sales_tax", "bank"]
    assert lines[0].debit + lines[1].debit == lines[2].credit


async def test_execute_rejects_non_approved(client, db_session):
    pa = _pa(status="draft")
    db_session.add(pa)
    await db_session.flush()
    r = await _execute(client, "pa_dir", pa.id)
    assert r.status_code == 409


async def test_execute_rejects_unauthorized_role(client, db_session):
    pa = _pa()
    db_session.add(pa)
    await db_session.flush()
    r = await _execute(client, "pa_dir", pa.id, role="requester")
    assert r.status_code == 403


async def test_execute_allows_role_management_assignment(client, db_session):
    """Finance BP is a role_management assignment, not a JWT role (memory: approval
    roles are assignments). A 'requester' JWT holding the finance_bp assignment pays."""
    payer_id = str(uuid.uuid4())
    db_session.add(CompanyConfig(role_management={"finance_bp_user_ids": [payer_id]}))
    pa = _pa()
    db_session.add(pa)
    await db_session.flush()

    r = await _execute(client, "pa_dir", pa.id, role="requester", user_id=payer_id)
    assert r.status_code == 200, r.text


async def test_execute_404_unknown_doc(client):
    r = await _execute(client, "pa_dir", uuid.uuid4())
    assert r.status_code == 404


# ── A0: claim follow-ups moved into the executor ───────────────────────────────

from app.models.mirrors import ExpenseApprovalEvent, ExpenseLineItem, User  # noqa: E402


async def test_claim_payment_writes_audit_row_and_books_budget(client, db_session, monkeypatch):
    """Phase a A0 — the executor now owns the OA audit row + budget booking
    (expense-api's /pay is a pure forward)."""
    import app.crud.payment_execute as pe

    booked = {}
    async def _fake_book(**kw):
        booked.update(kw)
        return {"idempotent": False}
    monkeypatch.setattr(pe.budget_client, "book_expense", _fake_book)

    payer_id = uuid.uuid4()
    db_session.add(User(id=payer_id, email="clerk@crm.ca", full_name="AP Clerk"))
    claim = _claim()
    db_session.add(claim)
    await db_session.flush()
    cc_id, acct_id = uuid.uuid4(), uuid.uuid4()
    db_session.add_all([
        ExpenseLineItem(claim_id=claim.id, budget_account_id=acct_id,
                        cost_center_id=cc_id, net_amount=Decimal("60.00")),
        ExpenseLineItem(claim_id=claim.id, budget_account_id=acct_id,
                        cost_center_id=cc_id, net_amount=Decimal("40.00")),
    ])
    await db_session.flush()

    r = await _execute(client, "expense_claim", claim.id, user_id=str(payer_id),
                       notes="paid by wire")
    assert r.status_code == 200, r.text

    ev = (await db_session.execute(
        select(ExpenseApprovalEvent).where(ExpenseApprovalEvent.claim_id == claim.id)
    )).scalar_one()
    assert ev.action == "pay"
    assert ev.actor_name == "AP Clerk"
    assert ev.to_status == "paid"

    assert booked["source_doc_type"] == "expense_claim"   # historical idempotency key
    assert booked["source_doc_id"] == claim.id
    assert len(booked["lines"]) == 1                      # aggregated by (account, cc)
    assert booked["lines"][0]["amount"] == Decimal("100.00")


# ── SoD: self_payment (Phase 0-B4, FIN-AUD-003) ────────────────────────────────

from app.models.mirrors import SodRule  # noqa: E402


async def test_self_payment_blocked_for_pa_creator(client, db_session):
    db_session.add(SodRule(rule_code="self_payment", name="self payment", enabled=True))
    payer = str(uuid.uuid4())
    pa = _pa()
    pa.created_by = uuid.UUID(payer)
    db_session.add(pa)
    await db_session.flush()

    r = await _execute(client, "pa_dir", pa.id, user_id=payer)
    assert r.status_code == 403
    assert "self_payment" in r.json()["detail"]


async def test_self_payment_blocked_for_claimant(client, db_session):
    db_session.add(SodRule(rule_code="self_payment", name="self payment", enabled=True))
    payer = str(uuid.uuid4())
    claim = _claim()
    claim.employee_id = uuid.UUID(payer)
    db_session.add(claim)
    await db_session.flush()

    r = await _execute(client, "expense_claim", claim.id, user_id=payer)
    assert r.status_code == 403


async def test_self_payment_allowed_when_rule_disabled(client, db_session):
    """The rule is config: disabling it (an audited act in identity) lifts the block."""
    db_session.add(SodRule(rule_code="self_payment", name="self payment", enabled=False))
    payer = str(uuid.uuid4())
    pa = _pa()
    pa.created_by = uuid.UUID(payer)
    db_session.add(pa)
    await db_session.flush()

    r = await _execute(client, "pa_dir", pa.id, user_id=payer)
    assert r.status_code == 200, r.text


async def test_other_payer_unaffected_by_sod(client, db_session):
    db_session.add(SodRule(rule_code="self_payment", name="self payment", enabled=True))
    pa = _pa()
    db_session.add(pa)
    await db_session.flush()

    r = await _execute(client, "pa_dir", pa.id)  # random payer ≠ creator
    assert r.status_code == 200, r.text
