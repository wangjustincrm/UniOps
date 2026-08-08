"""Batch BOM-existence check (MRP phase0 continuous-forecast Task 9,
spec §8b): a finished good can be forecast before its BOM exists in mdm-api
— the Sales Forecast grid and MPS lines need a single, cheap batch call to
flag which product codes still have zero approved BOMs, instead of each
page hammering /boms/effective per row (which also 404s on a miss, forcing
error-based control flow for a routine "not yet" state).

POST /boms/exist gate matches the OTHER read boms endpoints (/effective,
/explode, /where-used, /sync-state) — mrp.report.view, not bare
authentication (see boms.py module docstring: a BOM is a trade secret).
This suite seeds rows directly via the Bom model rather than round-tripping
through POST /boms/sync, since only Bom.product_material_code/status matter
here, not the sync/transform pipeline already covered by
test_bom_sync_and_effective.py.
"""
import pytest

from app.models.bom import Bom


async def _seed_bom(db_session, *, product_material_code, status, nc_source_pk):
    bom = Bom(
        product_material_code=product_material_code,
        status=status,
        nc_source_pk=nc_source_pk,
    )
    db_session.add(bom)
    await db_session.commit()
    return bom


@pytest.mark.anyio
async def test_exist_returns_only_products_with_an_approved_bom(client, db_session):
    await _seed_bom(db_session, product_material_code="S0093", status="approved", nc_source_pk="H-S0093")
    # A draft/inactive bom for S9999 must NOT count as "has a BOM" — only
    # approved rows do (matches /effective's own status filter).
    await _seed_bom(db_session, product_material_code="S9999", status="draft", nc_source_pk="H-S9999")

    resp = await client.post("/mdm/v1/boms/exist", json={"product_codes": ["S0093", "S9999"]})
    assert resp.status_code == 200
    assert resp.json() == {"with_bom": ["S0093"]}


@pytest.mark.anyio
async def test_exist_dedupes_multiple_approved_versions_of_the_same_product(client, db_session):
    """A product with several coexisting approved versions (the real-world
    norm — survey §8, e.g. CS0026 has 7 approved versions) must appear only
    ONCE in with_bom, not once per version row."""
    await _seed_bom(db_session, product_material_code="CS0026", status="approved", nc_source_pk="H-CS-1")
    await _seed_bom(db_session, product_material_code="CS0026", status="approved", nc_source_pk="H-CS-2")

    resp = await client.post("/mdm/v1/boms/exist", json={"product_codes": ["CS0026"]})
    assert resp.status_code == 200
    assert resp.json() == {"with_bom": ["CS0026"]}


@pytest.mark.anyio
async def test_exist_empty_product_codes_returns_empty(client, db_session):
    resp = await client.post("/mdm/v1/boms/exist", json={"product_codes": []})
    assert resp.status_code == 200
    assert resp.json() == {"with_bom": []}
