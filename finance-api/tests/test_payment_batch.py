"""Payment batches (payment runs) + partial-payment invoice flip (Phase a A4)."""
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.mirrors import ExpenseClaim, Invoice
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord


def _token(role="finance_manager"):
    return jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="finance_manager"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _pa(amount="100.00", status="approved", currency="CAD", po_id=None,
        pa_type="regular", invoice_ids=None) -> PaymentApplication:
    return PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="t", pa_type=pa_type,
        status=status, po_id=po_id, po_number="PO-1" if po_id else None,
        vendor_id=uuid.uuid4(), vendor_name="ACME", invoice_ids=invoice_ids or [],
        payment_amount=Decimal(amount), currency=currency, created_by=uuid.uuid4(),
    )


def _claim(amount="100.00", status="approved", currency="CAD") -> ExpenseClaim:
    return ExpenseClaim(
        claim_number=f"EXP-{uuid.uuid4().hex[:8]}", claim_type="EXP", status=status,
        employee_id=uuid.uuid4(), employee_name="Jane Doe", currency=currency,
        total_amount=Decimal(amount), tax_amount=Decimal("0"), net_amount=Decimal(amount),
    )


# ── payment batches ─────────────────────────────────────────────────────────────

async def test_due_lists_approved_pas(client, db_session):
    db_session.add_all([_pa("100.00"), _pa("50.00", status="draft")])
    await db_session.flush()
    r = await client.get("/finance/v1/payments/due", headers=_h())
    assert r.status_code == 200
    assert all(row["doc_number"] for row in r.json())
    assert len(r.json()) >= 1 and all(Decimal(row["amount"]) > 0 for row in r.json())


async def test_create_and_execute_batch(client, db_session):
    a, b = _pa("100.00"), _pa("200.00")
    db_session.add_all([a, b])
    await db_session.flush()

    r = await client.post("/finance/v1/payments/batches", headers=_h(),
                          json={"docs": [{"doc_kind": "pa_dir", "doc_id": str(a.id)},
                                         {"doc_kind": "pa_dir", "doc_id": str(b.id)}]})
    assert r.status_code == 201, r.text
    batch = r.json()
    assert batch["total"] == "300.00" and batch["status"] == "draft"

    r2 = await client.post(f"/finance/v1/payments/batches/{batch['id']}/execute",
                           headers=_h("ap_clerk"))
    assert r2.status_code == 200, r2.text
    assert r2.json()["paid"] == 2 and r2.json()["failed"] == 0

    await db_session.refresh(a); await db_session.refresh(b)
    assert a.status == "processed" and b.status == "processed"
    recs = (await db_session.execute(
        select(PaymentRecord).where(PaymentRecord.doc_id.in_([a.id, b.id])))).scalars().all()
    assert len(recs) == 2 and all(r.batch_id is not None for r in recs)


async def test_create_batch_rejects_mixed_currency(client, db_session):
    a, b = _pa("100.00", currency="CAD"), _pa("100.00", currency="USD")
    db_session.add_all([a, b])
    await db_session.flush()
    r = await client.post("/finance/v1/payments/batches", headers=_h(),
                          json={"docs": [{"doc_kind": "pa_dir", "doc_id": str(a.id)},
                                         {"doc_kind": "pa_dir", "doc_id": str(b.id)}]})
    assert r.status_code == 409 and "single-currency" in r.json()["detail"]


async def test_batch_isolates_line_failure(client, db_session):
    """A closed period fails one line; the rest still pay (savepoint per line)."""
    from app.models.fiscal_period import FiscalPeriod
    good = _pa("100.00")
    db_session.add(good)
    await db_session.flush()
    r = await client.post("/finance/v1/payments/batches", headers=_h(),
                          json={"docs": [{"doc_kind": "pa_dir", "doc_id": str(good.id)}]})
    batch_id = r.json()["id"]

    # close the batch's period so execution fails for that line
    db_session.add(FiscalPeriod(period=date.today().strftime("%Y-%m"), status="hard_closed"))
    await db_session.flush()

    r2 = await client.post(f"/finance/v1/payments/batches/{batch_id}/execute", headers=_h())
    assert r2.status_code == 200
    assert r2.json()["paid"] == 0 and r2.json()["failed"] == 1
    assert "closed" in r2.json()["lines"][0]["error"]


# ── A4b partial payment ──────────────────────────────────────────────────────────

async def test_prepayment_pa_leaves_invoice_partially_paid(client, db_session):
    inv = Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}", vendor_invoice_number="VI",
        vendor_id=uuid.uuid4(), vendor_name="ACME", amount=Decimal("1000.00"),
        tax_amount=Decimal("0"), total_amount=Decimal("1000.00"), currency="CAD",
        invoice_date=date(2026, 6, 1), due_date=date(2026, 7, 1), status="matched")
    db_session.add(inv)
    await db_session.flush()

    # 50% prepayment PA (PA-PO) referencing the invoice
    prepay = _pa("500.00", po_id=uuid.uuid4(), pa_type="prepayment",
                 invoice_ids=[str(inv.id)])
    db_session.add(prepay)
    await db_session.flush()
    r = await client.post("/finance/v1/payments/execute", headers=_h("ap_clerk"),
                          json={"doc_kind": "pa", "doc_id": str(prepay.id)})
    assert r.status_code == 200, r.text
    await db_session.refresh(inv)
    assert inv.status == "partially_paid"

    # NOTE: the open-items view now reads finance-owned ap_invoices, while the
    # payment executor still flips the EPMS mirror Invoice. The mirror→ap_invoice
    # payment bridge is Plan 2; until then this PA's mirror invoice is not an
    # ap_invoice, so the open-items coupling assertion was removed here.

    # 50% balance PA closes it
    balance = _pa("500.00", po_id=prepay.po_id, pa_type="balance",
                  invoice_ids=[str(inv.id)])
    db_session.add(balance)
    await db_session.flush()
    r = await client.post("/finance/v1/payments/execute", headers=_h("ap_clerk"),
                          json={"doc_kind": "pa", "doc_id": str(balance.id)})
    assert r.status_code == 200, r.text
    await db_session.refresh(inv)
    assert inv.status == "paid"


# ── claims in payment batches ────────────────────────────────────────────────────

async def test_due_includes_approved_claims(client, db_session):
    db_session.add_all([_pa("100.00"), _claim("75.00")])
    await db_session.flush()
    r = await client.get("/finance/v1/payments/due", headers=_h())
    assert r.status_code == 200
    rows = r.json()
    claim_rows = [x for x in rows if x["doc_kind"] == "expense_claim"]
    assert len(claim_rows) == 1
    assert claim_rows[0]["payee"] == "Jane Doe"
    assert all("payee" in x for x in rows)


async def test_batch_with_claim_executes_and_flips_status(client, db_session):
    pa, claim = _pa("100.00"), _claim("75.00")
    db_session.add_all([pa, claim])
    await db_session.flush()

    r = await client.post("/finance/v1/payments/batches", headers=_h(),
                          json={"docs": [{"doc_kind": "pa_dir", "doc_id": str(pa.id)},
                                         {"doc_kind": "expense_claim", "doc_id": str(claim.id)}]})
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "175.00"

    r2 = await client.post(f"/finance/v1/payments/batches/{r.json()['id']}/execute",
                           headers=_h("ap_clerk"))
    assert r2.status_code == 200, r2.text
    assert r2.json()["paid"] == 2 and r2.json()["failed"] == 0

    await db_session.refresh(pa); await db_session.refresh(claim)
    assert pa.status == "processed"
    assert claim.status == "paid"
