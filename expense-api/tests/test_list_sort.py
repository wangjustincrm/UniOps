"""Click-to-sort on the paged OA lists: GET /api/v1/expenses (Expense Claims,
Travel Applications) and GET /api/v1/invoices/all (EPMS "From OA" tab).

Contract (same in every UniOps list endpoint): `sort=<key>&dir=asc|desc`,
unknown key -> 422, empty values last in BOTH directions, no sort -> the
previous default order (newest first), and the sort covers the whole list
before the page is cut, so page 2 continues page 1.

Rows are scoped to a fresh user per test (own claims / own uploads), because
the test DB is shared with the rest of the suite.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

import app.db.base as db_module
from app.core.config import settings
from app.main import create_app
from app.models.expense import ExpenseClaim
from app.models.invoice import ExpenseInvoice

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _client_for(role: str, user_id: str) -> AsyncClient:
    token = jwt.encode(
        {"sub": user_id, "role": role, "type": "access", "exp": datetime.utcnow() + timedelta(hours=8)},
        settings.jwt_secret_key, algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _add(*rows):
    async with db_module.AsyncSessionLocal() as db:
        db.add_all(rows)
        await db.commit()


# ── /api/v1/expenses ─────────────────────────────────────────────────────────

async def _seed_claims(owner: str, claim_type: str = "EXP") -> None:
    uid = uuid.UUID(owner)
    tag = uuid.uuid4().hex[:6]
    spec = [
        # key, employee_name, net, status, submitted_at offset (None=draft), created offset, destination
        ("b", "bob", Decimal("20.00"), "submitted", 5, 1, "Toronto"),
        ("a", "Amy", Decimal("300.00"), "draft", None, 3, None),
        ("c", "carl", Decimal("4.50"), "in_review", 1, 0, ""),
        ("d", "Dee", Decimal("75.00"), "approved", 2, 2, "montreal"),
    ]
    await _add(*[
        ExpenseClaim(
            claim_number=f"{claim_type}-SRT{tag}-{k}", claim_type=claim_type, employee_id=uid,
            employee_name=name, department_name="Ops", submission_date=date(2026, 3, 1),
            net_amount=net, total_amount=net, status=st, created_by=uid,
            travel_destination=dest,
            submitted_at=(T0 + timedelta(days=sub)) if sub is not None else None,
            created_at=T0 + timedelta(days=cr),
        )
        for k, name, net, st, sub, cr, dest in spec
    ])


def _keys(body):
    return [i["claim_number"].rsplit("-", 1)[1] for i in body["items"]]


async def _list(client, **params):
    r = await client.get("/api/v1/expenses", params={"page_size": 50, **params})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_expenses_sort_employee_and_amount_both_directions():
    owner = str(uuid.uuid4())
    await _seed_claims(owner)
    async with _client_for("employee", owner) as c:
        assert _keys(await _list(c, sort="employee_name")) == ["a", "b", "c", "d"]   # case-insensitive
        assert _keys(await _list(c, sort="employee_name", dir="desc")) == ["d", "c", "b", "a"]
        assert _keys(await _list(c, sort="net_amount")) == ["c", "b", "d", "a"]      # numeric, not text
        assert _keys(await _list(c, sort="net_amount", dir="desc")) == ["a", "d", "b", "c"]


@pytest.mark.asyncio
async def test_expenses_date_column_is_submitted_else_created_and_default_unchanged():
    owner = str(uuid.uuid4())
    await _seed_claims(owner)
    async with _client_for("employee", owner) as c:
        # submitted_at ?? created_at: c=1, d=2, a(draft, created)=3, b=5
        assert _keys(await _list(c, sort="date")) == ["c", "d", "a", "b"]
        # Default: created_at desc, as before.
        assert _keys(await _list(c)) == ["a", "d", "b", "c"]


@pytest.mark.asyncio
async def test_expenses_empty_destination_last_both_directions_travel_list():
    owner = str(uuid.uuid4())
    await _seed_claims(owner, claim_type="TRA")
    async with _client_for("employee", owner) as c:
        asc = _keys(await _list(c, type="TRA", sort="travel_destination"))
        desc = _keys(await _list(c, type="TRA", sort="travel_destination", dir="desc"))
    assert asc[:2] == ["d", "b"] and set(asc[2:]) == {"a", "c"}    # montreal, Toronto, then NULL / ''
    assert desc[:2] == ["b", "d"] and set(desc[2:]) == {"a", "c"}


@pytest.mark.asyncio
async def test_expenses_admin_branch_sorts_too_and_pages_continue():
    owner = str(uuid.uuid4())
    await _seed_claims(owner)
    async with _client_for("system_admin", owner) as c:
        p = dict(my_claims="true", sort="status", dir="asc", page_size=2)
        r1 = await _list(c, **p, page=1)
        r2 = await _list(c, **p, page=2)
    assert r1["total"] == 4
    # approved, draft, in_review, submitted
    assert _keys(r1) + _keys(r2) == ["d", "a", "c", "b"]


@pytest.mark.asyncio
async def test_expenses_unknown_sort_key_is_422():
    owner = str(uuid.uuid4())
    async with _client_for("employee", owner) as c:
        assert (await c.get("/api/v1/expenses", params={"sort": "employee_id"})).status_code == 422
        assert (await c.get("/api/v1/expenses", params={"sort": "net_amount", "dir": "sideways"})).status_code == 422
    async with _client_for("system_admin", owner) as c:
        assert (await c.get("/api/v1/expenses", params={"sort": "1; select 1"})).status_code == 422


# ── /api/v1/invoices/all (OA source) ─────────────────────────────────────────

async def _seed_oa_invoices(owner: str) -> None:
    uid = uuid.UUID(owner)
    spec = [
        # key, invoice_number, vendor, total, invoice_date, created offset
        ("b", "INV-2", "beta", Decimal("20"), date(2026, 1, 5), 1),
        ("a", None, "Alpha", Decimal("300"), None, 3),
        ("c", "inv-10", None, Decimal("4.5"), date(2026, 1, 1), 0),
        ("d", "INV-1", "delta", Decimal("75"), date(2026, 1, 9), 2),
    ]
    await _add(*[
        ExpenseInvoice(file_name=f"{k}.pdf", file_mime_type="application/pdf", file_size_bytes=1,
                       invoice_number=num, vendor_name=v, total_amount=tot, invoice_date=d,
                       created_by=uid, created_at=T0 + timedelta(days=off))
        for k, num, v, tot, d, off in spec
    ])


async def _oa(client, **params):
    r = await client.get("/api/v1/invoices/all", params={"source": "oa", "page_size": 50, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _files(body):
    return [i["file_name"][0] for i in body["items"]]


@pytest.mark.asyncio
async def test_unified_invoices_sort_whole_list_nulls_last_both_directions():
    owner = str(uuid.uuid4())
    await _seed_oa_invoices(owner)
    async with _client_for("requester", owner) as c:
        assert _files(await _oa(c)) == ["a", "d", "b", "c"]                      # default: newest first
        assert _files(await _oa(c, sort="vendor_name")) == ["a", "b", "d", "c"]  # c has no vendor -> last
        assert _files(await _oa(c, sort="vendor_name", dir="desc")) == ["d", "b", "a", "c"]
        assert _files(await _oa(c, sort="total_amount", dir="desc")) == ["a", "d", "b", "c"]
        assert _files(await _oa(c, sort="invoice_date")) == ["c", "b", "d", "a"]
        assert _files(await _oa(c, sort="invoice_date", dir="desc")) == ["d", "b", "c", "a"]
        p = dict(sort="total_amount", dir="asc", page_size=2)
        r1, r2 = await _oa(c, **p, page=1), await _oa(c, **p, page=2)
    assert r1["total"] == 4
    assert _files(r1) + _files(r2) == ["c", "b", "d", "a"]


@pytest.mark.asyncio
async def test_unified_invoices_unknown_sort_key_is_422():
    async with _client_for("requester", str(uuid.uuid4())) as c:
        r = await c.get("/api/v1/invoices/all", params={"sort": "created_by"})
    assert r.status_code == 422
