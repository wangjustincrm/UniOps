"""material_suppliers CRUD — supply parameters, hand-maintained (MRP phase0 task 6).

Phase 1 purchase-suggestion logic picks the default supplier + lead time via
`is_primary` (see app/api/v1/material_suppliers.py docstring). This suite
exercises the HTTP surface per the task-6-brief test contract: create, list
filtered by material_code, duplicate-pair rejection (409, not a raw 500 from
the DB unique constraint), partial update, and delete.
"""
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.material_supplier import MaterialSupplier


@pytest.mark.anyio
async def test_create_and_list_filtered_by_material_code(client: AsyncClient, db_session):
    resp = await client.post("/mdm/v1/material-suppliers", json={
        "material_code": "CM0040",
        "partner_code": "0000131",
        "lead_time_days": 45,
        "moq": "500",
        "is_primary": True,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["material_code"] == "CM0040"
    assert body["partner_code"] == "0000131"
    assert body["lead_time_days"] == 45
    assert body["moq"] == "500.0000" or body["moq"] == "500"  # Decimal -> string
    assert body["is_primary"] is True

    row = (await db_session.execute(
        select(MaterialSupplier).where(MaterialSupplier.material_code == "CM0040")
    )).scalar_one()
    assert row.partner_code == "0000131"
    assert row.lead_time_days == 45
    assert row.moq == Decimal("500")
    assert row.is_primary is True

    list_resp = await client.get("/mdm/v1/material-suppliers", params={"material_code": "CM0040"})
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert {"items", "total", "page", "page_size"} <= list_body.keys()
    assert list_body["total"] == 1
    assert list_body["items"][0]["material_code"] == "CM0040"
    assert list_body["items"][0]["partner_code"] == "0000131"


@pytest.mark.anyio
async def test_duplicate_material_partner_pair_returns_409(client: AsyncClient):
    payload = {
        "material_code": "CM0041",
        "partner_code": "0000132",
        "lead_time_days": 30,
    }
    first = await client.post("/mdm/v1/material-suppliers", json=payload)
    assert first.status_code == 201, first.text

    dup = await client.post("/mdm/v1/material-suppliers", json=payload)
    assert dup.status_code == 409


@pytest.mark.anyio
async def test_patch_updates_mutable_fields(client: AsyncClient):
    created = await client.post("/mdm/v1/material-suppliers", json={
        "material_code": "CM0042",
        "partner_code": "0000133",
        "lead_time_days": 20,
        "is_primary": False,
    })
    assert created.status_code == 201, created.text
    row_id = created.json()["id"]

    patched = await client.patch(f"/mdm/v1/material-suppliers/{row_id}", json={
        "lead_time_days": 60,
        "is_primary": True,
        "notes": "preferred vendor",
    })
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["lead_time_days"] == 60
    assert body["is_primary"] is True
    assert body["notes"] == "preferred vendor"
    # unrelated fields untouched
    assert body["material_code"] == "CM0042"
    assert body["partner_code"] == "0000133"


@pytest.mark.anyio
async def test_only_one_primary_supplier_per_material_on_create(client: AsyncClient, db_session):
    """Partial unique index (migration 0013): at most one is_primary=true
    row per material_code — a second CREATE with is_primary=True for the
    same material must conflict (409), not silently create two "defaults"
    that Phase 1's purchase-suggestion logic would then pick between
    arbitrarily."""
    first = await client.post("/mdm/v1/material-suppliers", json={
        "material_code": "CM0050", "partner_code": "0000140", "is_primary": True,
    })
    assert first.status_code == 201, first.text

    # A second, non-primary supplier for the same material is fine —
    # multiple candidate suppliers are the normal case.
    second = await client.post("/mdm/v1/material-suppliers", json={
        "material_code": "CM0050", "partner_code": "0000141", "is_primary": False,
    })
    assert second.status_code == 201, second.text

    # A second PRIMARY supplier for the same material must conflict.
    third = await client.post("/mdm/v1/material-suppliers", json={
        "material_code": "CM0050", "partner_code": "0000142", "is_primary": True,
    })
    assert third.status_code == 409, third.text


@pytest.mark.anyio
async def test_only_one_primary_supplier_per_material_on_patch(client: AsyncClient, db_session):
    """The same constraint must fire on PATCH: promoting a second supplier
    to is_primary=True for a material that already has one must conflict."""
    existing_primary = await client.post("/mdm/v1/material-suppliers", json={
        "material_code": "CM0051", "partner_code": "0000150", "is_primary": True,
    })
    assert existing_primary.status_code == 201, existing_primary.text

    other = await client.post("/mdm/v1/material-suppliers", json={
        "material_code": "CM0051", "partner_code": "0000151", "is_primary": False,
    })
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]

    promoted = await client.patch(f"/mdm/v1/material-suppliers/{other_id}", json={"is_primary": True})
    assert promoted.status_code == 409, promoted.text


@pytest.mark.anyio
async def test_delete_removes_row(client: AsyncClient, db_session):
    created = await client.post("/mdm/v1/material-suppliers", json={
        "material_code": "CM0043",
        "partner_code": "0000134",
    })
    assert created.status_code == 201, created.text
    row_id = created.json()["id"]

    deleted = await client.delete(f"/mdm/v1/material-suppliers/{row_id}")
    assert deleted.status_code == 204

    remaining = (await db_session.execute(
        select(MaterialSupplier).where(MaterialSupplier.material_code == "CM0043")
    )).scalars().all()
    assert remaining == []

    # deleting again -> 404
    again = await client.delete(f"/mdm/v1/material-suppliers/{row_id}")
    assert again.status_code == 404


# ── 批量录入（供 MRP 的 Supply Parameters 页粘贴 Excel）────────────────────
#
# 采购手上的提前期/起订量是一张 Excel。逐行调 POST 意味着 200 次往返和一半
# 成功一半失败的中间态；批量入口按自然键 (material_code, partner_code) upsert，
# 并逐行报错 —— 一行写错不该让另外 199 行白填。


@pytest.mark.anyio
async def test_bulk_creates_and_updates_by_natural_key(client):
    first = await client.post("/mdm/v1/material-suppliers/bulk", json={"rows": [
        {"material_code": "CR0001", "partner_code": "SUP-A", "lead_time_days": 30},
        {"material_code": "CR0002", "partner_code": "SUP-B", "lead_time_days": 45, "moq": "500"},
    ]})
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 2
    assert first.json()["updated"] == 0

    again = await client.post("/mdm/v1/material-suppliers/bulk", json={"rows": [
        {"material_code": "CR0001", "partner_code": "SUP-A", "lead_time_days": 21},
    ]})
    assert again.status_code == 200, again.text
    assert again.json()["updated"] == 1
    assert again.json()["created"] == 0

    rows = (await client.get("/mdm/v1/material-suppliers?material_code=CR0001")).json()["items"]
    assert rows[0]["lead_time_days"] == 21


@pytest.mark.anyio
async def test_a_bad_row_is_reported_without_losing_the_good_ones(client):
    r = await client.post("/mdm/v1/material-suppliers/bulk", json={"rows": [
        {"material_code": "CR0010", "partner_code": "SUP-A", "lead_time_days": 30},
        {"material_code": "", "partner_code": "SUP-B", "lead_time_days": 10},
        {"material_code": "CR0011", "partner_code": "SUP-C", "lead_time_days": -5},
    ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert [e["row"] for e in body["errors"]] == [2, 3]
    assert any("lead time" in e["message"].lower() for e in body["errors"])

    # 好行确实落库了，不是「报错就整批回滚」
    kept = (await client.get("/mdm/v1/material-suppliers?material_code=CR0010")).json()["items"]
    assert len(kept) == 1


@pytest.mark.anyio
async def test_a_second_primary_for_one_material_is_a_row_error_not_a_500(client):
    """★每个物料最多一个主供应商由部分唯一索引保证 —— 粘贴里撞上它必须是
    可读的行级错误，而不是把整批打成 500。"""
    await client.post("/mdm/v1/material-suppliers/bulk", json={"rows": [
        {"material_code": "CR0020", "partner_code": "SUP-A", "is_primary": True},
    ]})

    r = await client.post("/mdm/v1/material-suppliers/bulk", json={"rows": [
        {"material_code": "CR0020", "partner_code": "SUP-B", "is_primary": True},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 0
    assert any("primary" in e["message"].lower() for e in r.json()["errors"])
