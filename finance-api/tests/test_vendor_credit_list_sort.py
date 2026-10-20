"""GET /vendor-credits: server-side paging and click-to-sort.

The Vendor Credits page used to fetch one page of 100 per status and sort it
in the browser. It now pages with limit/offset (total = full count) and sends
`sort=<key>&dir=asc|desc`. Contract: unknown key -> 422, empties last in both
directions, no sort -> newest first (unchanged), page 2 continues page 1.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import insert

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.vendor_credit import VendorCredit

URL = "/finance/v1/vendor-credits"
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _h():
    token = jwt.encode({"sub": str(uuid.uuid4()), "role": "ap_clerk",
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


async def _seed(db_session):
    """Four available credits; created_at order (oldest first) is b, a, d, c."""
    spec = [
        # key, vendor_name, total, applied, po_number, credit_date, created offset (days)
        ("a", "Amazon", "300.00", "0.00", None, date(2026, 8, 3), 1),
        ("b", "beta Corp", "20.00", "5.00", "PO-2", date(2026, 8, 1), 0),
        ("c", "Costco", "4.50", "0.00", "", date(2026, 8, 9), 3),
        ("d", "delta", "75.00", "75.00", "PO-10", date(2026, 8, 5), 2),
    ]
    for k, vn, tot, app_, po, cd, off in spec:
        t, a = Decimal(tot), Decimal(app_)
        await db_session.execute(insert(VendorCredit.__table__).values(
            id=uuid.uuid4(), credit_number=f"VC-SRT-{k}", vendor_id=uuid.uuid4(),
            vendor_name=vn, vendor_credit_number=f"VCN-{k.upper()}", credit_date=cd,
            amount=t, tax_amount=Decimal("0"), total_amount=t, applied_amount=a,
            remaining_amount=t - a, status="available", po_number=po,
            uploaded_by=uuid.uuid4(), uploaded_at=T0, created_at=T0 + timedelta(days=off),
        ))
    await db_session.commit()


async def _keys(client, **params):
    r = await client.get(URL, params={"status": "available", **params}, headers=_h())
    assert r.status_code == 200, r.text
    return [i["credit_number"][-1] for i in r.json()["items"]], r.json()["total"]


@pytest.mark.anyio
async def test_default_order_unchanged_and_unpaged_call_still_gets_everything(client, db_session):
    await _seed(db_session)
    keys, total = await _keys(client)                     # no limit/offset/sort, as before
    assert (keys, total) == (["c", "d", "a", "b"], 4)     # newest created first


@pytest.mark.anyio
async def test_sort_text_amount_date_both_directions(client, db_session):
    await _seed(db_session)
    assert (await _keys(client, sort="vendor_name"))[0] == ["a", "b", "c", "d"]        # case-insensitive
    assert (await _keys(client, sort="vendor_name", dir="desc"))[0] == ["d", "c", "b", "a"]
    assert (await _keys(client, sort="remaining_amount"))[0] == ["d", "c", "b", "a"]   # 0, 4.5, 15, 300
    assert (await _keys(client, sort="total_amount", dir="desc"))[0] == ["a", "d", "b", "c"]
    assert (await _keys(client, sort="credit_date"))[0] == ["b", "a", "d", "c"]


@pytest.mark.anyio
async def test_empty_po_number_last_in_both_directions(client, db_session):
    await _seed(db_session)
    asc = (await _keys(client, sort="po_number"))[0]
    desc = (await _keys(client, sort="po_number", dir="desc"))[0]
    assert asc[:2] == ["d", "b"] and set(asc[2:]) == {"a", "c"}     # "PO-10" < "PO-2" as text
    assert desc[:2] == ["b", "d"] and set(desc[2:]) == {"a", "c"}   # NULL and '' stay last


@pytest.mark.anyio
async def test_paging_continues_and_total_is_full_count(client, db_session):
    await _seed(db_session)
    p1, t1 = await _keys(client, sort="vendor_name", dir="desc", limit=2, offset=0)
    p2, t2 = await _keys(client, sort="vendor_name", dir="desc", limit=2, offset=2)
    assert t1 == t2 == 4
    assert p1 + p2 == ["d", "c", "b", "a"]


@pytest.mark.anyio
async def test_unknown_sort_key_and_bad_dir_are_422(client, db_session):
    r = await client.get(URL, params={"sort": "uploaded_by"}, headers=_h())
    assert r.status_code == 422
    r = await client.get(URL, params={"sort": "vendor_name", "dir": "up"}, headers=_h())
    assert r.status_code == 422
