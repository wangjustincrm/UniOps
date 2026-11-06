"""Data Maintenance list (GET /admin/{entity}) sorts by any list column it shows:
?sort=<column>&dir=asc|desc in SQL before paging, unknown / non-list column →
422, no sort → the registry's default order (created_at desc). Every
payment_batch list column is NOT NULL, so NULL placement is covered by the
identical code in epms-api/vms-api tests."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.payment_batch import PaymentBatch


def _h(role="system_admin"):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_admin_entity_list_sort(client, db_session):
    tag = f"SRT{uuid.uuid4().hex[:6]}"
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # (suffix, payment_method, total, batch_date day, minutes)
    for sfx, method, total, day, m in [("b", "cheque", "10.00", 3, 1),
                                       ("a", "Bank_transfer", "200.00", 1, 2),
                                       ("c", "wire", "9.50", 2, 3)]:
        db_session.add(PaymentBatch(
            batch_number=f"{tag}-{sfx}", batch_date=date(2026, 1, day), status="draft",
            currency="CAD", total=Decimal(total), payment_method=method,
            created_by=uuid.uuid4(), created_at=t0 + timedelta(minutes=m)))
    await db_session.flush()

    ents = (await client.get("/finance/v1/admin/entities", headers=_h())).json()
    pb = next(e for e in ents if e["key"] == "payment_batch")
    assert pb["sortable_columns"] == pb["list_columns"]

    async def sfx(qs):
        r = await client.get(f"/finance/v1/admin/payment_batch?search={tag}&{qs}", headers=_h())
        assert r.status_code == 200, r.text
        return [i["batch_number"][-1] for i in r.json()["items"]]

    assert await sfx("") == ["c", "a", "b"]                                  # created_at desc
    assert await sfx("sort=batch_number&dir=desc") == ["c", "b", "a"]
    assert await sfx("sort=total") == ["c", "b", "a"]                       # numeric, not text
    assert await sfx("sort=batch_date") == ["a", "c", "b"]
    assert await sfx("sort=payment_method") == ["a", "b", "c"]              # case-insensitive
    p1 = await sfx("sort=total&dir=desc&page=1&page_size=2")
    p2 = await sfx("sort=total&dir=desc&page=2&page_size=2")
    assert p1 + p2 == ["a", "b", "c"]
    r = await client.get(f"/finance/v1/admin/payment_batch?search={tag}&sort=total&page_size=1",
                         headers=_h())
    assert r.json()["total"] == 3
    assert (await client.get("/finance/v1/admin/payment_batch?sort=currency",
                             headers=_h())).status_code == 422
    assert (await client.get("/finance/v1/admin/payment_batch?sort=total&dir=x",
                             headers=_h())).status_code == 422
    assert (await client.get("/finance/v1/admin/nope?sort=total", headers=_h())).status_code == 404
