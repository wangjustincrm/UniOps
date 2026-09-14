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
                   status="approved", placed_days_ago=1, created_days_ago=None):
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
        if created_days_ago is not None:
            # created_at is the default period axis, so age it explicitly rather
            # than relying on the server default of "now".
            po.created_at = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
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
    # created_at, not placed_at: the latter is NULL on NC-mirrored orders.
    assert po["date_field"] == "created_at"


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

    # Narrowed to this test's own two rows. Asking for the first 100 POs made
    # the assertion depend on how much data every other test in the session had
    # left behind: the export tests seed a batch, the batch fills the cap, and a
    # test about row visibility starts failing for reasons of row count.
    body = {"entity": "purchase_order", "select": ["number"], "limit": 100,
            "where": [{"field": "number", "op": "like", "value": "PO-ASST-"}]}

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


async def test_count_alone_counts_rows(test_engine, admin_client):
    """Regression: count(*) with no other metric and no group_by.

    count uses a literal_column, which binds to no table. Without an explicit
    select_from, SQLAlchemy had nothing to infer a FROM clause from and emitted
    a table-less SELECT count(*) — which returns 1 regardless of how many rows
    exist. Every earlier test asked for count alongside a real column or a
    group_by, either of which supplies the FROM and hides this completely.
    """
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    for i in range(3):
        await _seed_po(test_engine, vendor, uid, number=f"PO-CNT-{tag}-{i}")

    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "metrics": ["count"],
        "where": [{"field": "number", "op": "like", "value": f"PO-CNT-{tag}"}],
    })
    assert r.status_code == 200
    assert r.json()["rows"][0]["count"] == 3


async def test_count_alone_respects_the_row_scope(test_engine, admin_client,
                                                  requester_client):
    """And the bare count must still be scoped — a table-less count(*) would
    have sailed past the row filter entirely."""
    vendor = await _seed_vendor(test_engine)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, _user_id(admin_client), number=f"PO-SC-{tag}-a")
    await _seed_po(test_engine, vendor, _user_id(admin_client), number=f"PO-SC-{tag}-b")

    body = {"entity": "purchase_order", "metrics": ["count"],
            "where": [{"field": "number", "op": "like", "value": f"PO-SC-{tag}"}]}

    admin = await admin_client.post("/api/v1/assistant/query", json=body)
    assert admin.json()["rows"][0]["count"] == 2

    req = await requester_client.post("/api/v1/assistant/query", json=body)
    assert req.json()["rows"][0]["count"] == 0, "scope must apply to a bare count"


async def test_a_bare_aggregate_reports_how_many_rows_it_matched(
    test_engine, admin_client
):
    """sum() over zero rows returns NULL, which is indistinguishable from "rows
    existed but the value was empty" unless the count comes with it. Without
    this, anything narrating the result has to hedge across both readings."""
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "metrics": ["amount"],
        "where": [{"field": "number", "op": "eq", "value": "PO-DOES-NOT-EXIST-XYZ"}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["matched_rows"] == 0
    assert body["rows"][0]["amount"] is None
    # The bookkeeping column must not leak into the answer.
    assert "__matched_rows" not in body["rows"][0]


async def test_matched_rows_counts_real_matches(test_engine, admin_client):
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, uid, number=f"PO-MR-{tag}-1", total="10.00")
    await _seed_po(test_engine, vendor, uid, number=f"PO-MR-{tag}-2", total="15.00")

    body = (await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "metrics": ["amount"],
        "where": [{"field": "number", "op": "like", "value": f"PO-MR-{tag}"}],
    })).json()

    assert body["matched_rows"] == 2
    assert body["rows"][0]["amount"] == "25.00"


async def test_grouped_aggregates_do_not_carry_matched_rows(test_engine, admin_client):
    """Per-group counts are what the count metric is for; a single number across
    all groups would be misleading, so it is only attached to bare aggregates."""
    vendor = await _seed_vendor(test_engine)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, _user_id(admin_client), number=f"PO-GA-{tag}")

    body = (await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "group_by": ["vendor_name"], "metrics": ["count"],
        "where": [{"field": "number", "op": "like", "value": f"PO-GA-{tag}"}],
    })).json()

    assert "matched_rows" not in body
    assert body["rows"][0]["count"] == 1


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
                   created_days_ago=1)
    old = f"PO-PER-{tag}-old"
    await _seed_po(test_engine, vendor, uid, number=old, created_days_ago=200)

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


# ── walking relationships ─────────────────────────────────────────────────────


async def _seed_pr_with_dept(test_engine, creator, *, number, department):
    from app.models.pr import PurchaseRequest
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pr = PurchaseRequest(number=number, title=number, type=2, status="approved",
                             amount=Decimal("10"), created_by=creator,
                             department_name=department)
        db.add(pr)
        await db.commit()
        return pr.id


async def _link_po_to_pr(test_engine, po_number, pr_id):
    from sqlalchemy import update
    from app.models.po import PurchaseOrder
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(update(PurchaseOrder)
                         .where(PurchaseOrder.number == po_number)
                         .values(pr_id=pr_id))
        await db.commit()


async def test_group_by_a_field_one_hop_away(test_engine, admin_client):
    """Regression for a substituted column.

    Asked to group purchase orders by department, the planner used budget_code
    and headed the result "department" — purchase_orders has no department at
    all, it lives on the requisition. Now the hop is reachable, so the real
    answer exists and there is no reason to approximate.
    """
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    pr_id = await _seed_pr_with_dept(test_engine, uid, number=f"PR-HOP-{tag}",
                                     department="Engineering")
    await _seed_po(test_engine, vendor, uid, number=f"PO-HOP-{tag}-1", total="10.00")
    await _seed_po(test_engine, vendor, uid, number=f"PO-HOP-{tag}-2", total="15.00")
    await _link_po_to_pr(test_engine, f"PO-HOP-{tag}-1", pr_id)
    await _link_po_to_pr(test_engine, f"PO-HOP-{tag}-2", pr_id)

    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order",
        "group_by": ["originating_pr.department_name"],
        "metrics": ["count", "amount"],
        "where": [{"field": "number", "op": "like", "value": f"PO-HOP-{tag}"}],
    })
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["originating_pr.department_name"] == "Engineering"
    assert rows[0]["count"] == 2
    assert rows[0]["amount"] == "25.00"


async def test_select_across_a_hop(test_engine, admin_client):
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    pr_id = await _seed_pr_with_dept(test_engine, uid, number=f"PR-SEL-{tag}",
                                     department="Quality")
    await _seed_po(test_engine, vendor, uid, number=f"PO-SEL-{tag}")
    await _link_po_to_pr(test_engine, f"PO-SEL-{tag}", pr_id)

    body = (await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order",
        "select": ["number", "originating_pr.department_name"],
        "where": [{"field": "number", "op": "eq", "value": f"PO-SEL-{tag}"}],
    })).json()
    assert body["rows"][0]["originating_pr.department_name"] == "Quality"


async def test_a_row_whose_far_side_is_missing_is_kept_not_dropped(
    test_engine, admin_client
):
    """An unlinked PO must still count. Dropping it would quietly change the
    total, which is the failure this layer exists to prevent — so the join is
    LEFT and the far field reads null."""
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, uid, number=f"PO-ORPH-{tag}")  # no pr_id

    body = (await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order",
        "select": ["number", "originating_pr.department_name"],
        "where": [{"field": "number", "op": "eq", "value": f"PO-ORPH-{tag}"}],
    })).json()
    assert body["row_count"] == 1, "the order itself must not vanish"
    assert body["rows"][0]["originating_pr.department_name"] is None


async def test_two_hops_are_allowed(test_engine, admin_client):
    """invoice → order → requisition. There is no invoices.pr_id, so a question
    about an invoice's department has no shorter path."""
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "invoice",
        "group_by": ["order.originating_pr.department_name"],
        "metrics": ["count"],
        "limit": 5,
    })
    assert r.status_code == 200, r.text


async def test_an_undeclared_link_is_rejected(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["not_a_link.number"],
    })
    assert r.status_code == 422
    assert "Unknown link" in r.json()["detail"]


async def test_an_unknown_field_across_a_real_link_is_rejected(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["originating_pr.not_a_column"],
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "not_a_column" in detail
    assert "department_name" in detail, "should list what IS available over there"


async def test_hops_are_bounded(admin_client):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "invoice",
        "select": ["order.originating_pr.orders.receipts.number"],
    })
    assert r.status_code == 422
    assert "more than" in r.json()["detail"]


async def test_the_far_side_of_a_hop_is_scoped_too(
    test_engine, admin_client, requester_client
):
    """Visibility does not transfer along a link.

    A PO can be visible for reasons unrelated to its requisition — you created
    it, or a task landed on you — so reading its PR's fields must still go
    through the PR's own scope, or the hop becomes a way to read documents you
    cannot open.
    """
    vendor = await _seed_vendor(test_engine)
    tag = uuid.uuid4().hex[:6]
    # PR belongs to the admin; the PO is created by the requester, so the
    # requester can see the order but not the requisition behind it.
    pr_id = await _seed_pr_with_dept(test_engine, _user_id(admin_client),
                                     number=f"PR-SCOPE-{tag}", department="Finance")
    await _seed_po(test_engine, vendor, _user_id(requester_client),
                   number=f"PO-SCOPE-{tag}")
    await _link_po_to_pr(test_engine, f"PO-SCOPE-{tag}", pr_id)

    body = (await requester_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order",
        "select": ["number", "originating_pr.department_name"],
        "where": [{"field": "number", "op": "eq", "value": f"PO-SCOPE-{tag}"}],
    })).json()
    assert body["row_count"] == 1, "the requester's own PO is still visible"
    assert body["rows"][0]["originating_pr.department_name"] is None, (
        "a hop must not hand over fields from a document this user cannot open"
    )


# ── totals come from SQL, never from the model ───────────────────────────────


async def test_a_grouped_query_carries_its_own_total(test_engine, admin_client):
    """Regression for a silent arithmetic error.

    Given nine department subtotals and asked for the grand total, the model
    added them up itself and landed 2,000 over. Every subtotal was right, the
    sum was wrong, and the reply gave no sign. The total now comes from SQL.
    """
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    for amount in ("10.00", "15.50", "4.50"):
        await _seed_po(test_engine, vendor, uid,
                       number=f"PO-TOT-{tag}-{amount}", total=amount)

    body = (await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "group_by": ["number"],
        "metrics": ["count", "amount"],
        "where": [{"field": "number", "op": "like", "value": f"PO-TOT-{tag}"}],
    })).json()

    assert body["totals"]["amount"] == "30.00"
    assert body["totals"]["count"] == 3


async def test_the_total_covers_everything_not_just_the_returned_page(
    test_engine, admin_client
):
    """A capped list must not produce a total of only what fitted — that is
    exactly the number someone would quote in a meeting."""
    vendor = await _seed_vendor(test_engine)
    uid = _user_id(admin_client)
    tag = uuid.uuid4().hex[:6]
    for i in range(5):
        await _seed_po(test_engine, vendor, uid, number=f"PO-CAP{i}-{tag}", total="10.00")

    body = (await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "group_by": ["number"], "metrics": ["amount"],
        "where": [{"field": "number", "op": "like", "value": tag}],
        "limit": 2,
    })).json()

    assert len(body["rows"]) == 2, "the page is capped"
    assert body["totals"]["amount"] == "50.00", "the total is not"


async def test_totals_respect_the_row_scope(test_engine, admin_client, requester_client):
    vendor = await _seed_vendor(test_engine)
    tag = uuid.uuid4().hex[:6]
    await _seed_po(test_engine, vendor, _user_id(requester_client),
                   number=f"PO-TS-{tag}-mine", total="10.00")
    await _seed_po(test_engine, vendor, _user_id(admin_client),
                   number=f"PO-TS-{tag}-theirs", total="99.00")

    body = (await requester_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "group_by": ["number"], "metrics": ["amount"],
        "where": [{"field": "number", "op": "like", "value": f"PO-TS-{tag}"}],
    })).json()
    assert body["totals"]["amount"] == "10.00", "a total must not sum invisible rows"


async def test_a_detail_query_has_no_totals(test_engine, admin_client):
    """Nothing to total, and an empty totals object would invite the model to
    fill it in."""
    body = (await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["number"], "limit": 2,
    })).json()
    assert "totals" not in body


# ── coded values ──────────────────────────────────────────────────────────────


async def test_a_result_carries_the_meaning_of_its_codes(test_engine, admin_client):
    """Whatever writes the reply sees the result and nothing else.

    The schema endpoint already describes `type`, but only the planner reads
    that; the narrating call is handed the rows. The mapping was built, put in
    the ontology, and threaded through — and the answer still came back as
    "type 2", because the result itself did not carry it.
    """
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_request", "group_by": ["type"], "metrics": ["count"],
    })
    assert r.status_code == 200
    assert r.json()["value_labels"]["type"]["4"] == "Service"


async def test_an_uncoded_result_carries_no_mapping(test_engine, admin_client):
    """Absent rather than empty, so the narrating prompt stays small on the
    overwhelming majority of queries that have no coded column at all."""
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_request", "select": ["number"], "limit": 1,
    })
    assert r.status_code == 200
    assert "value_labels" not in r.json()


async def test_the_schema_tells_the_planner_what_a_code_means(admin_client):
    """Without this the planner cannot turn "service purchases" into type=4,
    and answers a question about a real column by saying it does not exist."""
    r = await admin_client.get("/api/v1/assistant/schema")
    assert r.status_code == 200
    entities = {e["name"]: e for e in r.json()["entities"]}
    labels = entities["purchase_request"]["fields"]["type"]["value_labels"]
    assert labels["4"] == "Service"


# ── finance and MRP ───────────────────────────────────────────────────────────


async def test_the_ledger_is_invisible_without_view_finance(requester_client):
    """The access decision for finance is the permission and nothing else.

    These entities carry no row filter — a voucher is not "yours" the way a
    requisition is — so this gate is the only thing between a requester and the
    company's books. It is worth a test of its own rather than trusting that the
    generic gate covers it.
    """
    for entity in ("journal_voucher", "journal_voucher_line", "chart_of_account",
                   "ap_invoice", "bank_account", "business_partner"):
        r = await requester_client.post("/api/v1/assistant/query",
                                        json={"entity": entity, "metrics": ["count"]})
        assert r.status_code == 200, entity
        body = r.json()
        assert body["denied"] is True, f"{entity} answered a requester"
        assert not body["rows"], f"{entity} returned rows to a requester"


async def test_mrp_is_invisible_without_its_report_permission(requester_client):
    for entity in ("mrp_mps_line", "mrp_forecast_line",
                   "mrp_purchase_suggestion", "wms_inventory_lot"):
        r = await requester_client.post("/api/v1/assistant/query",
                                        json={"entity": entity, "metrics": ["count"]})
        assert r.status_code == 200, entity
        assert r.json()["denied"] is True, f"{entity} answered a requester"


async def test_the_schema_hides_what_the_caller_cannot_ask_about(requester_client):
    """A planner shown an entity it may not query will keep choosing it and keep
    being refused, and the person gets "I cannot answer that" for something that
    reads like a reasonable question. Better that it never appears."""
    r = await requester_client.get("/api/v1/assistant/schema")
    assert r.status_code == 200
    names = {e["name"] for e in r.json()["entities"]}
    assert not (names & {"journal_voucher", "ap_invoice", "bank_account",
                         "mrp_mps_line", "wms_inventory_lot"})
    # ...and still shows what they CAN ask about, or the test proves nothing.
    assert "purchase_request" in names
