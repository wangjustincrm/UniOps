"""Endpoint tests for PATCH /mdm/v1/erp/sync/interval and
GET /mdm/v1/erp/sync/schedule.

Modeled on epms-api/tests/test_nc_purchase_sync_scheduler.py's "the interval
endpoint" section — the three services (epms, finance, mdm) implement the
same 0..MAX_INTERVAL_MINUTES contract independently, and nothing but tests
like this guards them from drifting apart. mdm-api had none before this.

mdm-api's `client` fixture (tests/conftest.py) always overrides the token
payload to system_admin and reuses a single `db_session` for the whole
request — so round trips within one test see uncommitted writes via
SQLAlchemy's own autoflush/identity-map, no explicit commit needed. The
403 case re-overrides get_token_payload to a non-admin role for just that
request, same trick test_boms_read_authz.py uses elsewhere in this suite.
"""
import uuid

import pytest
import pytest_asyncio

from app.models.company_config import CompanyConfig
from app.tasks import erp_sync_scheduler as sched


@pytest_asyncio.fixture
async def company_config(db_session):
    """PATCH /interval writes to the singleton company_config row, which
    production always has and a freshly created test schema does not —
    seed it the way the app itself would find it."""
    cfg = CompanyConfig(erp_mdm_sync_interval_minutes=None)
    db_session.add(cfg)
    await db_session.flush()
    return cfg


@pytest.mark.anyio
async def test_zero_is_accepted(client, company_config):
    r = await client.patch("/mdm/v1/erp/sync/interval", json={"minutes": 0})
    assert r.status_code == 200
    assert r.json()["interval_minutes"] == 0


@pytest.mark.anyio
async def test_max_interval_is_accepted(client, company_config):
    r = await client.patch("/mdm/v1/erp/sync/interval",
                           json={"minutes": sched.MAX_INTERVAL_MINUTES})
    assert r.status_code == 200
    assert r.json()["interval_minutes"] == sched.MAX_INTERVAL_MINUTES


@pytest.mark.anyio
async def test_above_max_is_rejected(client, company_config):
    r = await client.patch("/mdm/v1/erp/sync/interval",
                           json={"minutes": sched.MAX_INTERVAL_MINUTES + 1})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_negative_is_rejected(client, company_config):
    r = await client.patch("/mdm/v1/erp/sync/interval", json={"minutes": -1})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_non_integer_body_is_rejected(client, company_config):
    r = await client.patch("/mdm/v1/erp/sync/interval", json={"minutes": "soon"})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_value_round_trips_through_schedule_get(client, company_config):
    r = await client.patch("/mdm/v1/erp/sync/interval", json={"minutes": 120})
    assert r.status_code == 200

    status = (await client.get("/mdm/v1/erp/sync/schedule")).json()
    assert status["interval_minutes"] == 120


@pytest.mark.anyio
async def test_setting_the_interval_is_system_admin_only(client, company_config):
    from app.core.deps import get_token_payload
    from app.main import app

    async def _non_admin():
        return {"sub": str(uuid.uuid4()), "role": "requester", "type": "access"}

    app.dependency_overrides[get_token_payload] = _non_admin
    try:
        r = await client.patch("/mdm/v1/erp/sync/interval", json={"minutes": 30})
    finally:
        # Restore the client fixture's own system_admin override so any
        # test running after this one in the same session isn't affected.
        async def _admin():
            return {"sub": str(uuid.uuid4()), "role": "system_admin", "type": "access"}
        app.dependency_overrides[get_token_payload] = _admin
    assert r.status_code == 403


@pytest.mark.anyio
async def test_reading_the_schedule_is_system_admin_only(client, company_config):
    from app.core.deps import get_token_payload
    from app.main import app

    async def _non_admin():
        return {"sub": str(uuid.uuid4()), "role": "requester", "type": "access"}

    app.dependency_overrides[get_token_payload] = _non_admin
    try:
        r = await client.get("/mdm/v1/erp/sync/schedule")
    finally:
        async def _admin():
            return {"sub": str(uuid.uuid4()), "role": "system_admin", "type": "access"}
        app.dependency_overrides[get_token_payload] = _admin
    assert r.status_code == 403
