"""Sync orchestrator tests using a stub ErpClient against a real DB session."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.erp_material import ErpMaterial
from app.models.erp_sync_state import ErpSyncState
from app.services import erp_sync as erp_sync_module
from app.services.erp_client import ErpError
from app.services.erp_sync import sync_kind


@pytest.fixture
def material_payload():
    return [{
        "part_NO": "M001",
        "description": "Raw milk powder 400g",
        "unit_MEAS": "PCS",
        "dim_QUALITY": "400g*12",
        "weight_NET": "0.0048",
        "weight_GROSS": "0.005935",
        "volume": "0.030012",
        "part_STATUS": "A",
        "itemMESType": "A-10",
        "rowversion": "2026-05-13 02:12:26",
    }]


@pytest.mark.anyio
async def test_sync_material_full_inserts_record(db_session: AsyncSession, material_payload):
    stub = AsyncMock()
    stub.fetch_materials.return_value = material_payload

    result = await sync_kind(db_session, "material", full=True, client=stub)

    assert result["status"] == "success"
    assert result["mode"] == "full"
    assert result["total"] == 1
    assert result["inserted"] == 1

    rows = (await db_session.execute(select(ErpMaterial))).scalars().all()
    assert len(rows) == 1
    assert rows[0].erp_part_no == "M001"
    assert rows[0].part_status == "A"

    state = await db_session.get(ErpSyncState, "material")
    assert state is not None
    assert state.last_status == "success"
    assert state.last_row_count == 1


@pytest.mark.anyio
async def test_sync_material_second_run_updates_existing(db_session: AsyncSession, material_payload):
    stub = AsyncMock()
    stub.fetch_materials.return_value = material_payload
    await sync_kind(db_session, "material", full=True, client=stub)

    updated = [dict(material_payload[0], description="UPDATED")]
    stub.fetch_materials.return_value = updated

    result = await sync_kind(db_session, "material", full=False, client=stub)
    assert result["updated"] == 1
    assert result["inserted"] == 0

    rows = (await db_session.execute(select(ErpMaterial))).scalars().all()
    assert rows[0].description == "UPDATED"


@pytest.mark.anyio
async def test_sync_marks_failed_state_on_erp_error(db_session: AsyncSession):
    stub = AsyncMock()
    stub.fetch_materials.side_effect = ErpError(40004, "boom")

    with pytest.raises(ErpError):
        await sync_kind(db_session, "material", full=True, client=stub)

    state = await db_session.get(ErpSyncState, "material")
    assert state is not None
    assert state.last_status == "failed"
    assert "boom" in (state.last_message or "")


@pytest.mark.anyio
async def test_sync_marks_failed_state_on_non_erp_error(db_session: AsyncSession, monkeypatch):
    """A failure that isn't ErpError (IntegrityError out of the upsert, an
    oversized field, ...) has to advance the anchor exactly like the ErpError
    path above does — otherwise the scheduler's is_due() keeps seeing the old
    last_synced_at forever and retries the same broken sync every tick
    instead of once per interval.

    Without the fix, sync_kind never calls _write_state on this path, so no
    ErpSyncState row for "material" is created at all and the assertion
    below (state is not None) is what catches the regression.
    """
    stub = AsyncMock()
    stub.fetch_materials.return_value = [{"part_NO": "M001", "description": "x"}]

    def boom_mapper(rec):
        raise RuntimeError("boom - not an ErpError")

    monkeypatch.setitem(erp_sync_module._KIND_CONFIG["material"], "mapper", boom_mapper)

    with pytest.raises(RuntimeError):
        await sync_kind(db_session, "material", full=True, client=stub)

    state = await db_session.get(ErpSyncState, "material")
    assert state is not None
    assert state.last_status == "failed"
    assert state.last_synced_at is not None
    assert "boom" in (state.last_message or "")
