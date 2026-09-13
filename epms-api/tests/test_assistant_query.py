"""Controlled query layer: the whitelist holds and the row scope filters.

The load-bearing test here is test_scope_filters_rather_than_denies. A scope bug
that returned nothing would pass a weaker "requester cannot see someone else's
PO" assertion while quietly breaking the feature, so that test proves both
directions at once: the requester sees their own PO and does not see the other.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor

pytestmark = pytest.mark.asyncio


def _user_id(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    payload = jwt.decode(token, settings.JWT_SECRET_KEY,
                         algorithms=[settings.JWT_ALGORITHM])
    return uuid.UUID(payload["sub"])


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_vendor(test_engine) -> Vendor:
    async with _factory(test_engine)() as db:
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Assistant Test Vendor",
                        category="Services", contact_name="A", contact_email="a@t.test")
        db.add(vendor)
        await db.commit()
        await db.refresh(vendor)
        return vendor


async def _seed_po(test_engine, vendor, creator, *, number, total="100.00",
                   status="approved", placed_days_ago=1):
    """Commit a PO the API can actually read.

    Deliberately NOT the psycopg2 `pg_cur` fixture: that connection is rolled
    back on teardown and never commits, so rows written through it are invisible
    to the async engine the endpoints use.
    """
    async with _factory(test_engine)() as db:
        po = PurchaseOrder(
            number=number, title=number, type=1, status=status,
            subtotal=Decimal(total), total=Decimal(total),
            vendor_id=vendor.id, vendor_name=vendor.name, created_by=creator,
            placed_at=datetime.now(timezone.utc) - timedelta(days=placed_days_ago),
        )
        db.add(po)
        await db.commit()
        return po.id


# ── schema ────────────────────────────────────────────────────────────────────


async def test_schema_describes_queryable_entities(admin_client):
    r = await admin_client.get("/api/v1/assistant/schema")
    assert r.status_code == 200
    names = {e["name"] for e in r.json()["entities"]}
    assert {"purchase_request", "purchase_order", "goods_receipt"} <= names

    po = next(e for e in r.json()["entities"] if e["name"] == "purchase_order")
    # The planner needs the labels and the enum values to build a sane query.
    assert po["fields"]["status"]["kind"] == "enum"
    assert "approved" in po["fields"]["status"]["values"]
    assert "amount" in po["metrics"]
    assert po["date_field"] == "placed_at"


async def test_schema_omits_columns_outside_the_whitelist(admin_client):
    r = await admin_client.get("/api/v1/assistant/schema")
    po = next(e for e in r.json()["entities"] if e["name"] == "purchase_order")
    # Columns that exist on the model but were never registered must not leak
    # into the description — the planner should not learn they are there.
    assert "nc_source_pk" not in po["fields"]
    assert "created_by" not in po["fields"]
    assert "notes" not in po["fields"]


# ── whitelist enforcement ─────────────────────────────────────────────────────


async def test_unknown_entity_is_rejected(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={"entity": "users"})
    assert r.status_code == 422
    assert "Unknown entity" in r.json()["detail"]


async def test_unknown_field_is_rejected_and_names_alternatives(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["number", "created_by"],
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "created_by" in detail
    # The message doubles as the planner's retry hint, so it must list what IS
    # available rather than only saying no.
    assert "number" in detail


async def test_unregistered_column_cannot_be_filtered_on(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order",
        "where": [{"field": "nc_source_pk", "op": "eq", "value": "x"}],
    })
    assert r.status_code == 422


async def test_operator_must_suit_the_field_kind(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order",
        "where": [{"field": "total", "op": "like", "value": "1"}],
    })
    assert r.status_code == 422
    assert "like" in r.json()["detail"]


async def test_malformed_value_is_rejected_not_silently_dropped(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order",
        "where": [{"field": "placed_at", "op": "gte", "value": "not-a-date"}],
    })
    assert r.status_code == 422


# ── row scope ─────────────────────────────────────────────────────────────────


async def test_scope_filters_rather_than_denies(
    test_engine, admin_client, requester_client
):
    """A requester sees their own PO and not someone else's.

    Both halves matter. Only asserting the second would also pass if the scope
    returned nothing at all, which is the failure mode this layer must not have.
    """
    vendor = await _seed_vendor(test_engine)
    mine = f"PO-ASST-MINE-{uuid.uuid4().hex[:6]}"
    theirs = f"PO-ASST-THEIRS-{uuid.uuid4().hex[:6]}"
    await _seed_po(test_engine, vendor, _user_id(requester_client), number=mine)
    await _seed_po(test_engine, vendor, _user_id(admin_client), number=theirs)

    body = {"entity": "purchase_order", "select": ["number"], "limit": 100}

    admin = await admin_client.post("/api/v1/assistant/query", json=body)
    assert admin.status_code == 200
    seen_by_admin = {row["number"] for row in admin.json()["rows"]}
    assert {mine, theirs} <= seen_by_admin, "unrestricted role should see both"

    req = await requester_client.post("/api/v1/assistant/query", json=body)
    assert req.status_code == 200
    payload = req.json()
    assert payload["denied"] is False, "requester holds view_po; this is a row filter"
    seen_by_requester = {row["number"] for row in payload["rows"]}
    assert mine in seen_by_requester, "requester must still see their own PO"
    assert theirs not in seen_by_requester, "row scope must hide the other one"


async def test_filter_cannot_widen_past_the_scope(
    test_engine, admin_client, requester_client
):
    """Naming another user's PO explicitly still returns nothing."""
    vendor = await _seed_vendor(test_engine)
    theirs = f"PO-ASST-EXPLICIT-{uuid.uuid4().hex[:6]}"
    await _seed_po(test_engine, vendor, _user_id(admin_client), number=theirs)

    r = await requester_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["number"],
        "where": [{"field": "number", "op": "eq", "value": theirs}],
    })
    assert r.status_code == 200
    assert r.json()["rows"] == []


# ── query features ────────────────────────────────────────────────────────────


async def test_aggregate_groups_and_sums(test_engine, admin_client):
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, uid, number=f"PO-AGG-{tag}-1", total="150.00")
    await _seed_po(test_engine, vendor, uid, number=f"PO-AGG-{tag}-2", total="250.00")

    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order",
        "group_by": ["vendor_name"],
        "metrics": ["amount", "count"],
        "where": [{"field": "number", "op": "like", "value": f"PO-AGG-{tag}"}],
    })
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    # Money is serialised as a string, matching every other UniOps endpoint.
    assert rows[0]["amount"] == "400.00"
    assert rows[0]["count"] == 2


async def test_unknown_metric_is_rejected(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "group_by": ["vendor_name"],
        "metrics": ["profit"],
    })
    assert r.status_code == 422
    assert "profit" in r.json()["detail"]


async def test_truncation_is_reported(test_engine, admin_client):
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    for i in range(3):
        await _seed_po(test_engine, vendor, uid, number=f"PO-CAP-{tag}-{i}")

    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["number"],
        "where": [{"field": "number", "op": "like", "value": f"PO-CAP-{tag}"}],
        "limit": 2,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 2
    # Without this flag a capped result reads as a complete answer.
    assert body["truncated"] is True


async def test_like_wildcards_supplied_by_the_planner_are_escaped(
    test_engine, admin_client
):
    """A '%' in the search term matches a literal '%', not everything."""
    vendor = await _seed_vendor(test_engine)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, _user_id(admin_client), number=f"PO-ESC-{tag}")

    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["number"],
        "where": [{"field": "number", "op": "like", "value": "PO-ESC-%"}],
    })
    assert r.status_code == 200
    assert r.json()["rows"] == []


async def test_relative_period_is_resolved_server_side(test_engine, admin_client):
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, uid, number=f"PO-PER-{tag}-new",
                   placed_days_ago=1)
    old = f"PO-PER-{tag}-old"
    await _seed_po(test_engine, vendor, uid, number=old, placed_days_ago=200)

    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["number"],
        "where": [{"field": "number", "op": "like", "value": f"PO-PER-{tag}"}],
        "period": {"last_n_months": 3},
    })
    assert r.status_code == 200
    numbers = {row["number"] for row in r.json()["rows"]}
    assert f"PO-PER-{tag}-new" in numbers
    assert old not in numbers


async def test_out_of_range_period_is_rejected(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "period": {"last_n_months": 999},
    })
    assert r.status_code == 422
