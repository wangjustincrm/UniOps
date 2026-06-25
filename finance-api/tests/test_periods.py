"""Fiscal periods API + payment close gate (Phase 0-B1.7)."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.fiscal_period import FiscalPeriod


def _token(role: str = "finance_manager") -> str:
    payload = {
        "sub": str(uuid.uuid4()), "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _h(role="finance_manager"):
    return {"Authorization": f"Bearer {_token(role)}"}


async def test_close_and_list(client):
    r = await client.post("/finance/v1/periods/2026-05/close", json={"hard": False}, headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "soft_closed"

    r = await client.get("/finance/v1/periods", headers=_h("ap_clerk"))
    assert r.status_code == 200
    assert any(p["period"] == "2026-05" and p["status"] == "soft_closed" for p in r.json())


async def test_reopen_soft_only(client):
    await client.post("/finance/v1/periods/2026-04/close", json={"hard": False}, headers=_h())
    r = await client.post("/finance/v1/periods/2026-04/reopen", headers=_h())
    assert r.status_code == 200
    assert r.json()["status"] == "open"

    await client.post("/finance/v1/periods/2026-03/close", json={"hard": True}, headers=_h())
    r = await client.post("/finance/v1/periods/2026-03/reopen", headers=_h())
    assert r.status_code == 409  # hard-closed periods cannot be reopened via API


async def test_close_requires_finance_role(client):
    r = await client.post("/finance/v1/periods/2026-02/close", json={"hard": False},
                          headers=_h("requester"))
    assert r.status_code == 403


async def test_invalid_period_format(client):
    r = await client.post("/finance/v1/periods/2026-13/close", json={"hard": False}, headers=_h())
    assert r.status_code == 422


async def test_hard_close_is_final(client):
    await client.post("/finance/v1/periods/2026-01/close", json={"hard": True}, headers=_h())
    r = await client.post("/finance/v1/periods/2026-01/close", json={"hard": False}, headers=_h())
    assert r.status_code == 409
