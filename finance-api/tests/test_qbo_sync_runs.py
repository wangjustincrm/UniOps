import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from app.models.qbo import QboSyncRun


@pytest.mark.asyncio
async def test_sync_run_roundtrips(db_session):
    run = QboSyncRun(
        mode="full",
        status="running",
        started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        counters={"Vendor": 0},
        watermarks={},
    )
    db_session.add(run)
    await db_session.commit()

    got = (await db_session.execute(select(QboSyncRun))).scalar_one()
    assert got.mode == "full"
    assert got.status == "running"
    assert got.counters == {"Vendor": 0}
    assert got.watermarks == {}
    assert got.error is None
