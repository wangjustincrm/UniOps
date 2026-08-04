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
