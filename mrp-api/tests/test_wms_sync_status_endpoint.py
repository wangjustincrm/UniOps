"""GET /api/v1/admin/wms-sync/status — how old the numbers on screen are.

The Inventory page used to print `as of <today>` next to stock figures. That
date was the shelf-life reference date, not the data's date: three weeks after
the last sync it still said today. This endpoint is the real answer, and it is
gated `mrp.report.view` rather than `mrp.param.write` on purpose — everyone who
can read the numbers has to be able to see their age.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.params import WMS_SYNC_INTERVAL_KEY, set_param
from app.models.sync_state import MrpSyncState


@pytest.mark.anyio
async def test_status_before_the_first_sync(client, auth_headers):
    r = await client.get("/api/v1/admin/wms-sync/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["last_synced_at"] is None
    assert body["last_success_at"] is None
    assert body["row_count"] == 0
    assert body["interval_minutes"] == 5  # the documented default
    # Nothing to count down from yet; the next tick will pick it up.
    assert body["next_due_at"] is None


@pytest.mark.anyio
async def test_status_reports_the_snapshot_age_and_the_next_run(client, auth_headers, db_session):
    synced = datetime.now(timezone.utc) - timedelta(minutes=3)
    db_session.add(MrpSyncState(source="wms", status="success", row_count=3452,
                                last_synced_at=synced, last_success_at=synced,
                                updated_at=synced))
    await set_param(db_session, WMS_SYNC_INTERVAL_KEY, 10, None)

    body = (await client.get("/api/v1/admin/wms-sync/status", headers=auth_headers)).json()
    assert body["status"] == "success"
    assert body["row_count"] == 3452
    assert body["interval_minutes"] == 10
    assert datetime.fromisoformat(body["next_due_at"]) == synced + timedelta(minutes=10)
    # Timezone-carrying, so the browser cannot render "3 minutes ago" as
    # "4 hours ago" by reading it as local time.
    assert datetime.fromisoformat(body["last_synced_at"]).tzinfo is not None


@pytest.mark.anyio
async def test_a_failed_attempt_does_not_make_the_data_look_fresh(client, auth_headers, db_session):
    """The distinction the whole mrp16 migration exists for: the attempt is
    recent, the data is not."""
    success = datetime.now(timezone.utc) - timedelta(hours=6)
    attempt = datetime.now(timezone.utc) - timedelta(minutes=2)
    db_session.add(MrpSyncState(
        source="wms", status="failed", row_count=0,
        last_error="ORA-12541: TNS:no listener",
        last_synced_at=attempt, last_success_at=success, updated_at=attempt))

    body = (await client.get("/api/v1/admin/wms-sync/status", headers=auth_headers)).json()
    assert body["status"] == "failed"
    assert body["last_error"].startswith("ORA-12541")
    assert datetime.fromisoformat(body["last_success_at"]) == success
    assert datetime.fromisoformat(body["last_synced_at"]) == attempt


@pytest.mark.anyio
async def test_interval_zero_reports_no_next_run(client, auth_headers, db_session):
    now = datetime.now(timezone.utc)
    db_session.add(MrpSyncState(source="wms", status="success", row_count=1,
                                last_synced_at=now, last_success_at=now, updated_at=now))
    await set_param(db_session, WMS_SYNC_INTERVAL_KEY, 0, None)

    body = (await client.get("/api/v1/admin/wms-sync/status", headers=auth_headers)).json()
    assert body["interval_minutes"] == 0
    assert body["next_due_at"] is None


@pytest.mark.anyio
async def test_status_requires_the_report_view_permission(client, non_admin_token, monkeypatch):
    from tests.test_permission_gates import _deny_everything

    _deny_everything(monkeypatch)
    r = await client.get("/api/v1/admin/wms-sync/status",
                         headers={"Authorization": f"Bearer {non_admin_token}"})
    assert r.status_code == 403


# ── the interval parameter ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_interval_round_trips(client, auth_headers):
    r = await client.put(f"/api/v1/params/{WMS_SYNC_INTERVAL_KEY}",
                         json={"value": 15}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()[WMS_SYNC_INTERVAL_KEY] == 15

    params = (await client.get("/api/v1/params", headers=auth_headers)).json()
    assert params[WMS_SYNC_INTERVAL_KEY] == 15


@pytest.mark.anyio
async def test_zero_is_accepted_as_the_off_switch(client, auth_headers):
    r = await client.put(f"/api/v1/params/{WMS_SYNC_INTERVAL_KEY}",
                         json={"value": 0}, headers=auth_headers)
    assert r.status_code == 200


@pytest.mark.parametrize("bad", [-1, 1441, "15", 1.5, True, None])
@pytest.mark.anyio
async def test_bad_intervals_are_rejected(client, auth_headers, bad):
    """`True` in particular: Python makes it equal 1, so an unguarded boolean
    would quietly mean a full snapshot every minute."""
    r = await client.put(f"/api/v1/params/{WMS_SYNC_INTERVAL_KEY}",
                         json={"value": bad}, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.anyio
async def test_a_second_sync_is_refused_while_one_is_running(
        client, auth_headers, db_engine, monkeypatch):
    """Single flight: run_wms_sync empties the mirror before refilling it, so
    an overlapping run would blank the snapshot the first one is writing (and
    the Inventory page is reading). The lock is held here on a SEPARATE
    connection, which is what a scheduler tick in another process looks like.
    """
    from sqlalchemy import text

    from app.api.v1 import admin_sync as admin_sync_module
    from app.services.wms_sync.lock import ADVISORY_LOCK_KEY

    monkeypatch.setattr(admin_sync_module, "wms_configured", lambda: True)

    async with db_engine.connect() as other:
        got = (await other.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                   {"k": ADVISORY_LOCK_KEY})).scalar_one()
        assert got is True
        try:
            r = await client.post("/api/v1/admin/wms-sync", headers=auth_headers)
        finally:
            await other.execute(text("SELECT pg_advisory_unlock(:k)"),
                                {"k": ADVISORY_LOCK_KEY})
    assert r.status_code == 409
