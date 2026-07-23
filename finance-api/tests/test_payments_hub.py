"""Payments hub — list, filters, summary, export.

`_rec` deliberately creates (and flushes) a real `PaymentApplication` row for
every non-claim-doc-kind record: `payment_records.pa_id` carries a genuine FK
to `payment_applications.id` (see app/models/payment.py), so a bare
`uuid.uuid4()` — as a naive reading of the task brief's snippet would use —
violates that constraint against the real Postgres test DB. This mirrors the
`_pa()` / `_record()` pattern already established in tests/test_remittance.py.
`batch_id` carries no such FK, so it stays a free-standing UUID.
"""
import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord


def _h(role="finance_manager"):
    token = jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _rec(db, *, doc_kind="pa", amount="100.00", currency="CAD", batch_id=None,
               payment_date=date(2026, 7, 22), method="bank_transfer", doc_id=None):
    pa_id = pa_number = vendor_id = vendor_name = None
    if doc_kind != "expense_claim":
        pa = PaymentApplication(
            pa_number="PA-1", title="t", pa_type="regular", status="approved",
            vendor_id=uuid.uuid4(), vendor_name="ACME", payment_amount=Decimal(amount),
            currency=currency, created_by=uuid.uuid4(),
        )
        db.add(pa)
        await db.flush()
        pa_id, pa_number, vendor_id, vendor_name = pa.id, pa.pa_number, pa.vendor_id, pa.vendor_name
    rec = PaymentRecord(
        doc_kind=doc_kind, doc_id=doc_id if doc_id is not None else uuid.uuid4(),
        doc_number="DOC-1", pa_id=pa_id, pa_number=pa_number, vendor_id=vendor_id,
        vendor_name=vendor_name, payment_date=payment_date, payment_method=method,
        amount=Decimal(amount), currency=currency, recorded_by=uuid.uuid4(),
        status="completed", batch_id=batch_id,
    )
    db.add(rec)
    await db.flush()
    return rec


async def test_list_serializes_expense_claim_payment(client, db_session):
    """Regression: PaymentResponse used to require pa_id / pa_number / vendor_id /
    vendor_name, all NULL for a claim payment, so this raised."""
    await _rec(db_session, doc_kind="expense_claim")

    r = await client.get("/finance/v1/payments", headers=_h())
    assert r.status_code == 200
    assert r.json()["items"][0]["pa_id"] is None


async def test_claim_payment_shows_the_employee_as_payee(client, db_session):
    from app.models.mirrors import ExpenseClaim
    claim = ExpenseClaim(claim_number="EXP-9", claim_type="EXP", status="paid",
                         employee_id=uuid.uuid4(), employee_name="Jane Doe",
                         currency="CAD", total_amount=Decimal("20.00"),
                         tax_amount=Decimal("0"), net_amount=Decimal("20.00"))
    db_session.add(claim)
    await db_session.flush()
    await _rec(db_session, doc_kind="expense_claim", amount="20.00", doc_id=claim.id)

    body = (await client.get("/finance/v1/payments", headers=_h())).json()
    assert body["items"][0]["payee_name"] == "Jane Doe"


async def test_source_filter_isolates_batched_from_single(client, db_session):
    """Dedicated to the `source` filter alone: two records identical on every
    other filterable dimension (same payment_date, currency, amount), one
    batched and one not, so only `source` can be responsible for narrowing
    the result. Both directions are asserted — a filter that silently
    ignored `source` would return both records for either query."""
    batch_id = uuid.uuid4()
    batched = await _rec(db_session, amount="10.00", currency="CAD",
                         payment_date=date(2026, 7, 15), batch_id=batch_id)
    single = await _rec(db_session, amount="10.00", currency="CAD",
                        payment_date=date(2026, 7, 15))

    batch_only = (await client.get("/finance/v1/payments?source=batch",
                                   headers=_h())).json()
    assert batch_only["total"] == 1
    assert batch_only["items"][0]["id"] == str(batched.id)
    assert batch_only["items"][0]["batch_id"] == str(batch_id)

    single_only = (await client.get("/finance/v1/payments?source=single",
                                    headers=_h())).json()
    assert single_only["total"] == 1
    assert single_only["items"][0]["id"] == str(single.id)
    assert single_only["items"][0]["batch_id"] is None


async def test_filters_compose(client, db_session):
    batch_id = uuid.uuid4()
    await _rec(db_session, amount="10.00", currency="CAD", batch_id=batch_id,
              payment_date=date(2026, 7, 1))
    await _rec(db_session, amount="20.00", currency="USD", payment_date=date(2026, 7, 20))
    await _rec(db_session, amount="30.00", currency="CAD", payment_date=date(2026, 7, 20))
    # Same date/currency window as the "30.00" record above, but batched —
    # so date_from/date_to + currency alone would still admit it, and only
    # `source=single` excludes it. This is what makes `source` an actual
    # participant in the composed filter below, not a no-op.
    await _rec(db_session, amount="40.00", currency="CAD", batch_id=uuid.uuid4(),
              payment_date=date(2026, 7, 20))

    r = (await client.get(
        "/finance/v1/payments?date_from=2026-07-10&date_to=2026-07-31"
        "&currency=CAD&source=single", headers=_h())).json()
    assert r["total"] == 1
    assert r["items"][0]["amount"] == "30.00"


async def test_summary_covers_whole_filter_not_page(client, db_session):
    for _ in range(3):
        await _rec(db_session, amount="10.00")
    await _rec(db_session, amount="5.00", currency="USD")

    rows = (await client.get("/finance/v1/payments/summary?page_size=1",
                             headers=_h())).json()
    by_ccy = {r["currency"]: r for r in rows}
    assert by_ccy["CAD"]["count"] == 3
    assert by_ccy["CAD"]["total"] == "30.00"
    assert by_ccy["USD"]["count"] == 1


async def test_export_respects_filters_and_ignores_pagination(client, db_session):
    for _ in range(3):
        await _rec(db_session, amount="10.00")
    await _rec(db_session, amount="5.00", currency="USD")

    r = await client.get("/finance/v1/payments/export?currency=CAD&page_size=1",
                         headers=_h())
    assert r.status_code == 200
    body = r.text.strip().splitlines()
    assert len(body) == 4                       # header + 3 rows
    assert body[0].startswith("payment_date,")


async def test_export_carries_claim_employee_as_payee(client, db_session):
    """Regression coverage for export_payments' use of _payee_names(): a claim
    payment has no vendor_name, so if the row builder ever reverted to
    `r.vendor_name or ""` the payee column would go blank for this row while
    every other assertion in this file stayed green (they only exercise
    doc_kind='pa' records)."""
    from app.models.mirrors import ExpenseClaim
    claim = ExpenseClaim(claim_number="EXP-10", claim_type="EXP", status="paid",
                         employee_id=uuid.uuid4(), employee_name="John Smith",
                         currency="CAD", total_amount=Decimal("15.00"),
                         tax_amount=Decimal("0"), net_amount=Decimal("15.00"))
    db_session.add(claim)
    await db_session.flush()
    await _rec(db_session, doc_kind="expense_claim", amount="15.00", doc_id=claim.id)

    r = await client.get("/finance/v1/payments/export", headers=_h())
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.text)))
    claim_rows = [row for row in rows if row["doc_kind"] == "expense_claim"]
    assert len(claim_rows) == 1
    assert claim_rows[0]["payee"] == "John Smith"


from app.models.remittance import KIND_VENDOR, SCOPE_PAYMENT, SENT, RemittanceNotification


# ── Fix 1: read authority (not just authentication) ─────────────────────────

async def test_list_summary_export_require_finance_read_authority(client, db_session):
    """An authenticated user with no finance role at all (e.g. an OA-only
    employee) must not be able to list, total, or export payment history —
    only a valid token, gating nothing, was the pre-fix behaviour."""
    await _rec(db_session, amount="10.00")

    r_list = await client.get("/finance/v1/payments", headers=_h("requester"))
    r_summary = await client.get("/finance/v1/payments/summary", headers=_h("requester"))
    r_export = await client.get("/finance/v1/payments/export", headers=_h("requester"))

    assert r_list.status_code == 403
    assert r_summary.status_code == 403
    assert r_export.status_code == 403


async def test_remittance_filter_splits_sent_from_not_sent(client, db_session):
    r1 = await _rec(db_session, amount="10.00")
    r2 = await _rec(db_session, amount="20.00")
    db_session.add(RemittanceNotification(
        scope_kind=SCOPE_PAYMENT, scope_id=r1.id, recipient_kind=KIND_VENDOR,
        party_id=uuid.uuid4(), party_name="ACME", email="ap@acme.test",
        payment_record_ids=[str(r1.id)], amount=Decimal("10.00"), currency="CAD",
        status=SENT, attempts=1, created_by=uuid.uuid4()))
    await db_session.flush()

    sent = (await client.get("/finance/v1/payments?remittance=sent", headers=_h())).json()
    assert [i["amount"] for i in sent["items"]] == ["10.00"]
    not_sent = (await client.get("/finance/v1/payments?remittance=not_sent",
                                 headers=_h())).json()
    assert [i["amount"] for i in not_sent["items"]] == ["20.00"]
