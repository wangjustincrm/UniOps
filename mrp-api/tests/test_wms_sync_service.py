"""run_wms_sync service tests: snapshot semantics, status mapping sourced
from the mrp_status_mapping table (seeded by migration mrp01), and
mrp_sync_state bookkeeping on success/failure.
"""
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select

from app.models.wms_inventory import WmsInventoryLot
from app.models.wms_lot_location import WmsLotLocation

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
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: [])

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
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: [])
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
async def test_wms_sync_expiry_cutoff_is_utc(db_session, monkeypatch):
    """The expiry cutoff (`today` in run_wms_sync) must come from
    datetime.now(timezone.utc).date(), not container-local time — otherwise
    which lots flip to 'expired' would depend on the host/container TZ.
    Asserted by spying on every `datetime.now()` call the sync makes
    (cutoff, sync_batch_id, sync_state timestamps) and requiring all of them
    pass `tz=timezone.utc` explicitly, never a bare/local call."""
    from datetime import datetime as real_datetime, timezone

    from app.services.wms_sync import service

    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: [])

    seen_tzs: list = []
    real_now = real_datetime.now

    class _SpyDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            seen_tzs.append(tz)
            return real_now(tz)

    monkeypatch.setattr(service, "datetime", _SpyDateTime)

    await service.run_wms_sync(db_session)

    assert seen_tzs, "datetime.now() was never called during sync"
    assert all(tz is timezone.utc for tz in seen_tzs), (
        f"every datetime.now() call in run_wms_sync must pass timezone.utc explicitly, got {seen_tzs}"
    )


@pytest.mark.anyio
async def test_wms_sync_refuses_to_replace_snapshot_with_empty_extract(db_session, monkeypatch):
    """An empty live extract must NOT delete-all + insert-0 the previous
    snapshot — Phase 1's purchase-suggestion logic reads wms_inventory_lots
    as "current stock"; a silent empty overwrite would make it propose
    buying material that is actually sitting in the warehouse. Seed a real
    snapshot first, then re-sync against an empty extract and assert
    nothing was deleted."""
    from app.models.sync_state import MrpSyncState
    from app.models.wms_inventory import WmsInventoryLot
    from app.services.wms_sync import service

    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: [])
    first = await service.run_wms_sync(db_session)
    assert first["lots"] == 2

    monkeypatch.setattr(service, "fetch_inventory", lambda: [])
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: [])
    second = await service.run_wms_sync(db_session)
    assert second.get("skipped") is True
    assert second["lots"] == 2  # reports the KEPT count, not 0

    n = (await db_session.execute(select(func.count()).select_from(WmsInventoryLot))).scalar()
    assert n == 2, "previous snapshot must survive an empty extract untouched"

    state = await db_session.get(MrpSyncState, "wms")
    assert state.status == "empty_extract"
    assert state.row_count == 2
    assert "0 rows" in state.last_error


@pytest.mark.anyio
async def test_wms_sync_records_last_error_on_failure(db_session, monkeypatch):
    from app.services.wms_sync import service
    from app.models.sync_state import MrpSyncState

    def _boom():
        raise RuntimeError("WMS connection refused")

    monkeypatch.setattr(service, "fetch_inventory", _boom)
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: [])

    with pytest.raises(RuntimeError, match="WMS connection refused"):
        await service.run_wms_sync(db_session)

    state = await db_session.get(MrpSyncState, "wms")
    assert state.status == "failed"
    assert "WMS connection refused" in state.last_error


# ── lot locations, synced alongside the lots (2026-08-18) ────────────────

FAKE_LOCATIONS = [
    {"warehouseid": "CANADA", "sku": "CF0086", "lotnum": "HGC1976532",
     "locationid": "11010345", "traceid": "00000002603",
     "qty": 400, "qtyallocated": 0, "qtyonhold": 0, "zoneid": "LIHG"},
    {"warehouseid": "CANADA", "sku": "CF0086", "lotnum": "HGC1976532",
     "locationid": "DM01", "traceid": "*",
     "qty": 20, "qtyallocated": 0, "qtyonhold": 0, "zoneid": "WOD"},
]


@pytest.mark.anyio
async def test_sync_mirrors_lot_locations(db_session, monkeypatch):
    """A lot really does sit in several places — one packaging lot in the live
    warehouse is spread over 28 — so this is its own grain, not a column."""
    from app.services.wms_sync import service

    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: FAKE_LOCATIONS)

    result = await service.run_wms_sync(db_session)
    assert result["lots"] == len(FAKE_ROWS)
    assert result["locations"] == 2

    rows = (await db_session.execute(
        sa.select(WmsLotLocation).order_by(WmsLotLocation.location_id))).scalars().all()
    assert [r.location_id for r in rows] == ["11010345", "DM01"]
    assert [r.zone_id for r in rows] == ["LIHG", "WOD"]
    # '*' is stored verbatim: it is part of the unique key, and NULLs do not
    # collide in a Postgres unique constraint, so normalising here would stop
    # the key catching a real duplicate. The API maps it for display.
    assert rows[1].trace_id == "*"


@pytest.mark.anyio
async def test_lot_locations_share_the_lots_sync_batch_id(db_session, monkeypatch):
    """★ Both tables are written in ONE transaction with ONE batch id. If they
    could drift apart, a lot would show stock sitting nowhere, or a location
    would hold a lot that no longer exists — and nothing would report it."""
    from app.services.wms_sync import service

    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: FAKE_LOCATIONS)
    await service.run_wms_sync(db_session)

    lot_batches = set((await db_session.execute(
        sa.select(WmsInventoryLot.sync_batch_id))).scalars())
    loc_batches = set((await db_session.execute(
        sa.select(WmsLotLocation.sync_batch_id))).scalars())
    assert lot_batches == loc_batches
    assert len(lot_batches) == 1


@pytest.mark.anyio
async def test_a_second_sync_replaces_locations_rather_than_appending(
    db_session, monkeypatch,
):
    """Snapshot semantics, same as the lots: stock that has moved must not
    linger in its old location."""
    from app.services.wms_sync import service

    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: FAKE_LOCATIONS)
    await service.run_wms_sync(db_session)

    moved = [{**FAKE_LOCATIONS[0], "locationid": "99999999"}]
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: moved)
    await service.run_wms_sync(db_session)

    rows = (await db_session.execute(
        sa.select(WmsLotLocation.location_id))).scalars().all()
    assert rows == ["99999999"], "the old locations must be gone, not merged"


@pytest.mark.anyio
async def test_an_empty_lot_extract_leaves_locations_alone_too(db_session, monkeypatch):
    """The empty-extract guard refuses to replace a real snapshot with nothing.
    It has to cover locations as well, or a WMS hiccup would empty the location
    table while the lots survived — every batch would then report that its stock
    is stored nowhere.
    """
    from app.services.wms_sync import service

    monkeypatch.setattr(service, "fetch_inventory", lambda: FAKE_ROWS)
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: FAKE_LOCATIONS)
    await service.run_wms_sync(db_session)


    monkeypatch.setattr(service, "fetch_inventory", lambda: [])
    monkeypatch.setattr(service, "fetch_lot_locations", lambda: [])
    result = await service.run_wms_sync(db_session)
    assert result.get("skipped") is True

    kept = (await db_session.execute(
        sa.select(sa.func.count()).select_from(WmsLotLocation))).scalar_one()
    assert kept == 2, "the previous location snapshot must survive"
