"""Endpoint tests for PATCH /finance/v1/nc-sync/interval and the two new
interval_minutes/next_due_at keys on GET /finance/v1/nc-sync/status.

Modeled on epms-api/tests/test_nc_purchase_sync_scheduler.py's "the interval
endpoint" section and on this file's sibling test_nc_sync.py (client/_h/_token
helpers copied verbatim from there rather than shared, matching that file's
own self-contained style — it doesn't factor them into conftest.py either).
The three services (epms, finance, mdm) implement the same
0..MAX_INTERVAL_MINUTES contract independently; nothing but tests like this
guards them from drifting apart, and finance-api had none before this.

★ NOT RUN. finance-api's full suite is running against the shared
finance_test database elsewhere right now; running these here would
interfere with that run. They're written to the same conventions as the
rest of this file's neighbors (test_nc_sync.py, test_nc_sync_scheduler.py)
and should be run by whoever owns that in-progress suite.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings as app_settings
from app.db.base import get_db
from app.main import app
from app.models.mirrors import CompanyConfig
from app.tasks import nc_sync_scheduler as sched


def _token(role="system_admin", sub=None):
    return jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      app_settings.jwt_secret_key, algorithm=app_settings.jwt_algorithm)


def _h(role="system_admin"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def company_config(db_session):
    """PATCH /interval writes to the singleton company_config row, which
    production always has and a freshly created test schema does not —
    seed it the way the app itself would find it."""
    cfg = CompanyConfig(nc_jv_sync_interval_minutes=None)
    db_session.add(cfg)
    await db_session.flush()
    return cfg


async def test_zero_is_accepted(client, company_config):
    r = await client.patch("/finance/v1/nc-sync/interval", json={"minutes": 0}, headers=_h())
    assert r.status_code == 200
    assert r.json()["interval_minutes"] == 0


async def test_max_interval_is_accepted(client, company_config):
    r = await client.patch("/finance/v1/nc-sync/interval",
                           json={"minutes": sched.MAX_INTERVAL_MINUTES}, headers=_h())
    assert r.status_code == 200
    assert r.json()["interval_minutes"] == sched.MAX_INTERVAL_MINUTES


async def test_above_max_is_rejected(client, company_config):
    r = await client.patch("/finance/v1/nc-sync/interval",
                           json={"minutes": sched.MAX_INTERVAL_MINUTES + 1}, headers=_h())
    assert r.status_code == 422


async def test_negative_is_rejected(client, company_config):
    r = await client.patch("/finance/v1/nc-sync/interval", json={"minutes": -1}, headers=_h())
    assert r.status_code == 422


async def test_non_integer_body_is_rejected(client, company_config):
    r = await client.patch("/finance/v1/nc-sync/interval", json={"minutes": "soon"}, headers=_h())
    assert r.status_code == 422


async def test_setting_the_interval_is_system_admin_only(client, company_config):
    r = await client.patch("/finance/v1/nc-sync/interval", json={"minutes": 30},
                           headers=_h(role="finance_manager"))
    assert r.status_code == 403


async def test_interval_round_trips_through_status(client, company_config, monkeypatch):
    for f in ("nc_host", "nc_service", "nc_user", "nc_password"):
        monkeypatch.setattr(app_settings, f, "x")
    r = await client.patch("/finance/v1/nc-sync/interval", json={"minutes": 120}, headers=_h())
    assert r.status_code == 200

    status = (await client.get("/finance/v1/nc-sync/status", headers=_h())).json()
    assert status["interval_minutes"] == 120


async def test_status_defaults_the_interval_when_nobody_has_chosen(client, company_config):
    """A fresh company_config row has NULL, which must read as the default
    rather than as 0 — 0 means somebody deliberately switched it off."""
    status = (await client.get("/finance/v1/nc-sync/status", headers=_h())).json()
    assert status["interval_minutes"] == sched.DEFAULT_INTERVAL_MINUTES


async def test_status_reports_no_next_run_when_disabled(client, company_config):
    r = await client.patch("/finance/v1/nc-sync/interval", json={"minutes": 0}, headers=_h())
    assert r.status_code == 200
    status = (await client.get("/finance/v1/nc-sync/status", headers=_h())).json()
    assert status["interval_minutes"] == 0
    assert status["next_due_at"] is None
