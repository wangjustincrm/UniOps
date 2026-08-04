"""materials master table — upsert-from-ERP sync + list endpoint (MRP phase0 task 2).

erp_material's real columns (app/models/erp_material.py) differ from the
guessed ones in the task brief: erp_part_no/description/unit_meas/dim_quality/
item_mes_type, no exp/part_product_family. See material_sync.py's docstring
for the resulting mapping decisions.
"""
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.erp_material import ErpMaterial
from app.models.material import Material


@pytest.mark.anyio
async def test_material_sync_upserts_from_erp_mirror(client: AsyncClient, db_session):
    db_session.add(ErpMaterial(
        erp_part_no="CF0086",
        description="Skim Milk Powder",
        unit_meas="KGM",
        dim_quality="25kg",
        item_mes_type="10",
        raw_payload={"part_NO": "CF0086"},
        synced_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    resp = await client.post("/mdm/v1/materials/sync")
    assert resp.status_code == 200
    assert resp.json()["created"] == 1

    m = (await db_session.execute(
        select(Material).where(Material.code == "CF0086")
    )).scalar_one()
    assert m.base_uom == "KGM"
    assert m.spec == "25kg"
    assert m.erp_item_type == "10"
    assert m.erp_id == "CF0086"

    # idempotent: second run updates the existing row, doesn't duplicate
    resp2 = await client.post("/mdm/v1/materials/sync")
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["created"] == 0
    assert body2["updated"] == 1

    rows = (await db_session.execute(
        select(Material).where(Material.code == "CF0086")
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_materials_list_pagination(client: AsyncClient):
    resp = await client.get("/mdm/v1/materials", params={"page": 1, "page_size": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert {"items", "total", "page", "page_size"} <= body.keys()
