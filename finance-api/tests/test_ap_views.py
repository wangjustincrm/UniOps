"""AP open items + aging views (Phase a A2, FIN-AP-006)."""
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
from app.models.ap_invoice import ApInvoice


def _token():
    return jwt.encode({"sub": str(uuid.uuid4()), "role": "ap_clerk",
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h():
    return {"Authorization": f"Bearer {_token()}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _inv(vendor, due_days_ago: int, total="100.00", status="posted", vid=None) -> ApInvoice:
    today = date.today()
    return ApInvoice(
        ap_invoice_number=f"AP-{uuid.uuid4().hex[:12]}",
        source="epms", source_invoice_id=uuid.uuid4(),
        source_ref=f"INV-{uuid.uuid4().hex[:8]}",
        # Unique per row — (vendor_id, vendor_invoice_number) carries a partial
        # unique index (uq_ap_invoices_vendor_invno) since migration 0022.
        vendor_invoice_number=f"VI-{uuid.uuid4().hex[:8]}",
        vendor_id=vid or uuid.uuid4(), vendor_name=vendor,
        amount=Decimal(total), tax_amount=Decimal("0"), total_amount=Decimal(total),
        currency="CAD", invoice_date=today - timedelta(days=due_days_ago + 30),
        due_date=today - timedelta(days=due_days_ago), status=status,
    )


async def test_open_items_excludes_paid_and_orders_by_due(client, db_session):
    vid = uuid.uuid4()
    db_session.add_all([
        _inv("Zeta", 45, vid=vid),
        _inv("Zeta", 5, vid=vid),
        _inv("Zeta", 10, status="paid", vid=vid),
        _inv("Zeta", 1, status="draft", vid=vid),
    ])
    await db_session.flush()

    r = await client.get("/finance/v1/ap/open-items",
                         params={"vendor_id": str(vid)}, headers=_h())
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 2
    assert items[0]["days_overdue"] == 45    # oldest due first
    assert items[1]["days_overdue"] == 5


async def test_aging_buckets(client, db_session):
    vid = uuid.uuid4()
    db_session.add_all([
        _inv("BucketCo", -10, total="10.00", vid=vid),   # not due yet → current
        _inv("BucketCo", 15, total="20.00", vid=vid),    # 1-30
        _inv("BucketCo", 45, total="30.00", vid=vid),    # 31-60
        _inv("BucketCo", 75, total="40.00", vid=vid),    # 61-90
        _inv("BucketCo", 120, total="50.00", vid=vid),   # 90+
    ])
    await db_session.flush()

    r = await client.get("/finance/v1/ap/aging", headers=_h())
    assert r.status_code == 200
    row = next(x for x in r.json() if x["vendor_name"] == "BucketCo")
    assert row["current"] == "10.00"
    assert row["d1_30"] == "20.00"
    assert row["d31_60"] == "30.00"
    assert row["d61_90"] == "40.00"
    assert row["d90_plus"] == "50.00"
    assert row["total"] == "150.00"
