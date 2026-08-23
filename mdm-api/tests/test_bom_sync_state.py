"""BOM sync state tracking + concurrency guard (MRP phase1a Task 7).

`nc_bom_sync` full extract fixture is a trimmed copy of
test_bom_sync_and_effective.py's `_extract()` — this file only cares about
sync-state/concurrency behavior, not the BOM cascade shape, so it keeps its
own minimal single-header/single-line extract.
"""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture(autouse=True)
def _nc_configured_by_default(monkeypatch):
    from app.api.v1 import boms as boms_module

    monkeypatch.setattr(boms_module, "nc_configured", lambda: True)


def _extract():
    return {
        "headers": [
            {"cbomid": "H1", "hcmaterialid": "MA", "hversion": "1.0", "fbillstatus": 1},
        ],
        "lines": [
            {"cbom_bid": "L1", "cbomid": "H1", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"},
        ],
        "repl": [],
        "material_codes": {"MA": "CS0001", "MC": "CR0001"},
    }


@pytest.mark.anyio
async def test_sync_state_absent_before_any_sync(client, db_session):
    resp = await client.get("/mdm/v1/boms/sync-state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "nc_bom"
    assert body["last_success_at"] is None
    assert body["last_error"] is None
    assert body["last_stats"] is None


@pytest.mark.anyio
async def test_successful_sync_records_state(client, db_session, monkeypatch):
    from app.services.nc_bom_sync import canonical_sync

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", _extract)

    sync_resp = await client.post("/mdm/v1/boms/sync")
    assert sync_resp.status_code == 200
    stats = sync_resp.json()

    state_resp = await client.get("/mdm/v1/boms/sync-state")
    assert state_resp.status_code == 200
    body = state_resp.json()
    assert body["source"] == "nc_bom"
    assert body["last_success_at"] is not None
    assert body["last_error"] is None
    assert body["last_stats"] == stats


@pytest.mark.anyio
async def test_failed_sync_persists_error_and_still_raises(client, db_session, monkeypatch):
    """The exception must NOT be swallowed (POST /sync should surface it as
    a 500, same as any other unhandled error), AND last_error must be
    recorded so the UI can show the most recent attempt failed — the two
    outcomes the brief explicitly requires together."""
    from app.services.nc_bom_sync import canonical_sync

    def _broken_extract():
        raise RuntimeError("NC connection reset mid-fetch")

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", _broken_extract)

    with pytest.raises(RuntimeError, match="NC connection reset mid-fetch"):
        await canonical_sync.sync_boms(db_session)

    state_resp = await client.get("/mdm/v1/boms/sync-state")
    assert state_resp.status_code == 200
    body = state_resp.json()
    assert body["last_error"] is not None and "NC connection reset mid-fetch" in body["last_error"]
    assert body["last_success_at"] is None  # never succeeded yet


@pytest.mark.anyio
async def test_failed_sync_does_not_clobber_prior_success(client, db_session, monkeypatch):
    """A later failed attempt must not erase the LAST GOOD sync's
    last_success_at/last_stats — the UI still needs to show "last synced N
    hours ago" even while flagging the most recent attempt failed."""
    from app.services.nc_bom_sync import canonical_sync

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", _extract)
    ok_resp = await client.post("/mdm/v1/boms/sync")
    assert ok_resp.status_code == 200
    ok_stats = ok_resp.json()

    def _broken_extract():
        raise RuntimeError("transient NC timeout")

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", _broken_extract)
    with pytest.raises(RuntimeError):
        await canonical_sync.sync_boms(db_session)

    state_resp = await client.get("/mdm/v1/boms/sync-state")
    body = state_resp.json()
    assert body["last_error"] is not None and "transient NC timeout" in body["last_error"]
    assert body["last_success_at"] is not None  # preserved from the earlier success
    assert body["last_stats"] == ok_stats  # preserved from the earlier success


@pytest.mark.anyio
async def test_concurrent_sync_second_call_gets_409(client, db_session, monkeypatch):
    """pg_try_advisory_lock is scoped to the holding backend SESSION — the
    same AsyncSession re-acquiring its own already-held lock would trivially
    succeed (Postgres advisory locks are re-entrant per session), so this
    opens a genuinely SEPARATE connection/session against the same test
    database to simulate a second, independent in-flight request, and holds
    the lock open on it (never committing) while the real call under test
    runs concurrently against `db_session`."""
    from app.services.nc_bom_sync import canonical_sync

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", _extract)

    url = os.environ["TEST_DATABASE_URL"]
    other_engine = create_async_engine(url)
    OtherSession = async_sessionmaker(other_engine, expire_on_commit=False)
    other_db = OtherSession()
    try:
        got = (await other_db.execute(
            text("SELECT pg_try_advisory_lock(hashtext('nc_bom_sync'))")
        )).scalar()
        assert got is True  # sanity: the other session really holds it

        with pytest.raises(canonical_sync.BomSyncInProgress):
            await canonical_sync.sync_boms(db_session)

        resp = await client.post("/mdm/v1/boms/sync")
        assert resp.status_code == 409
        assert "already running" in resp.json()["detail"]
    finally:
        await other_db.execute(text("SELECT pg_advisory_unlock(hashtext('nc_bom_sync'))"))
        await other_db.commit()
        await other_db.close()
        await other_engine.dispose()

    # Lock released -> a real sync now goes through cleanly.
    ok_resp = await client.post("/mdm/v1/boms/sync")
    assert ok_resp.status_code == 200


@pytest.mark.anyio
async def test_lock_contention_does_not_overwrite_prior_state(client, db_session, monkeypatch):
    """A 409 from lock contention is a benign "someone else is syncing"
    signal, not a data-sync failure — it must NOT be written to
    nc_sync_state (that would blank out or misrepresent real success/error
    history every time two people click Sync close together)."""
    from app.services.nc_bom_sync import canonical_sync

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", _extract)
    ok_resp = await client.post("/mdm/v1/boms/sync")
    assert ok_resp.status_code == 200
    ok_stats = ok_resp.json()

    url = os.environ["TEST_DATABASE_URL"]
    other_engine = create_async_engine(url)
    OtherSession = async_sessionmaker(other_engine, expire_on_commit=False)
    other_db = OtherSession()
    try:
        await other_db.execute(text("SELECT pg_try_advisory_lock(hashtext('nc_bom_sync'))"))

        resp = await client.post("/mdm/v1/boms/sync")
        assert resp.status_code == 409
    finally:
        await other_db.execute(text("SELECT pg_advisory_unlock(hashtext('nc_bom_sync'))"))
        await other_db.commit()
        await other_db.close()
        await other_engine.dispose()

    state_resp = await client.get("/mdm/v1/boms/sync-state")
    body = state_resp.json()
    assert body["last_stats"] == ok_stats
    assert body["last_error"] is None
