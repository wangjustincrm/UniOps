"""run_wms_sync service tests: snapshot semantics, status mapping sourced
from the mrp_status_mapping table (seeded by migration mrp01), and
mrp_sync_state bookkeeping on success/failure.
"""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

FAKE_ROWS = [
    {
        "warehouseid": "CANADA", "sku": "CF0086", "lotnum": "HGC1976532",
        "qty": Decimal("420"), "qtyallocated": Decimal("0"), "qtyonhold": Decimal("0"),
        "lotatt01": "2025-01-03", "lotatt02": "2027-01-02", "lotatt03": "2025-01-20",
        "lotatt05": "20250103 291041001", "lotatt08": "02", "lotatt13": "0000131",
        "lotatt14": "CASN2502100006*189", "edittime": None,
    },
    {
        "warehouseid": "CANADA", "sku": "CP0100", "lotnum": "HGC1976999",
        "qty": Decimal("10"), "qtyallocated": Decimal("2"), "qtyonhold": Decimal("1"),
        "lotatt01": "2024-01-03", "lotatt02": "2025-01-02", "lotatt03": "2024-01-20",
        "lotatt05": "20240103 291041002", "lotatt08": "01", "lotatt13": "0000132",
        "lotatt14": "CASN2402100007*190", "edittime": None,
    },
]


@pytest.mark.anyio
async def test_wms_sync_snapshot_idempotent(db_session, monkeypatch):
    from app.services.wms_sync import service
    from app.models.wms_inventory import WmsInventoryLot
    from app.models.sync_state import MrpSyncState

    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)

    r1 = await service.run_wms_sync(db_session)
    assert r1["lots"] == 2

    n = (await db_session.execute(select(func.count()).select_from(WmsInventoryLot))).scalar()
    assert n == 2

    state = await db_session.get(MrpSyncState, "wms")
    assert state.status == "success"
    assert state.row_count == 2
    assert state.last_error is None
    assert state.last_synced_at is not None

    # re-run must not duplicate rows (snapshot replace, not accumulate)
    r2 = await service.run_wms_sync(db_session)
    assert r2["lots"] == 2
    n2 = (await db_session.execute(select(func.count()).select_from(WmsInventoryLot))).scalar()
    assert n2 == 2


@pytest.mark.anyio
async def test_wms_sync_maps_status_from_db_table(db_session, monkeypatch):
    """Status mapping comes from mrp_status_mapping (seeded 01->hold,
    02->available, 04->hold by migration mrp01), not a hardcoded dict in the
    service."""
    from app.services.wms_sync import service
    from app.models.wms_inventory import WmsInventoryLot

    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)
    await service.run_wms_sync(db_session)

    lot_release = (await db_session.execute(
        select(WmsInventoryLot).where(WmsInventoryLot.lot_no == "HGC1976532")
    )).scalar_one()
    assert lot_release.mapped_status == "available"
    assert lot_release.wms_status == "02"
    assert lot_release.material_code == "CF0086"
    assert lot_release.sync_batch_id

    lot_block = (await db_session.execute(
        select(WmsInventoryLot).where(WmsInventoryLot.lot_no == "HGC1976999")
    )).scalar_one()
    # LOTATT02='2025-01-02' is in the past regardless of when this test runs
    # (repo's earliest plausible "today" is 2026), so this asserts the
    # expiry-override path, not the raw 01->hold mapping — see the sibling
    # transform test for the non-expired hold case.
    assert lot_block.mapped_status == "expired"


@pytest.mark.anyio
async def test_wms_sync_records_last_error_on_failure(db_session, monkeypatch):
    from app.services.wms_sync import service
    from app.models.sync_state import MrpSyncState

    def _boom():
        raise RuntimeError("WMS connection refused")

    monkeypatch.setattr(service, "fetch_inventory", _boom)

    with pytest.raises(RuntimeError, match="WMS connection refused"):
        await service.run_wms_sync(db_session)

    state = await db_session.get(MrpSyncState, "wms")
    assert state.status == "failed"
    assert "WMS connection refused" in state.last_error
