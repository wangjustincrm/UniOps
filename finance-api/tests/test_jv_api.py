"""JV lifecycle endpoints — Plan 2."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.journal_voucher import JournalVoucher
from app.services.posting import emit_event


def _token(role="finance_manager", sub=None):
    return jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="finance_manager", sub=None):
    return {"Authorization": f"Bearer {_token(role, sub)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _draft_jv(db):
    ev_id = await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": "5000",
                "debit": Decimal("100.00"), "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "2000",
                "credit": Decimal("100.00"), "currency": "CAD"}])
    return (await db.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()


async def test_list_and_get_detail(client, db_session):
    jv = await _draft_jv(db_session)
    r = await client.get("/finance/v1/journal-vouchers", headers=_h())
    assert r.status_code == 200
    assert any(row["id"] == str(jv.id) for row in r.json())

    r2 = await client.get(f"/finance/v1/journal-vouchers/{jv.id}", headers=_h())
    assert r2.status_code == 200
    body = r2.json()
    assert body["voucher"]["jv_number"].startswith("JV-")
    assert len(body["lines"]) == 2


async def test_review_then_post_endpoints(client, db_session):
    jv = await _draft_jv(db_session)
    r = await client.post(f"/finance/v1/journal-vouchers/{jv.id}/review", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reviewed"
    r2 = await client.post(f"/finance/v1/journal-vouchers/{jv.id}/post", headers=_h())
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "posted"


async def test_review_endpoint_forbidden_for_non_finance(client, db_session):
    jv = await _draft_jv(db_session)
    r = await client.post(f"/finance/v1/journal-vouchers/{jv.id}/review",
                          headers=_h(role="requester"))
    assert r.status_code == 403


async def test_post_batch_endpoint(client, db_session):
    jv = await _draft_jv(db_session)
    await client.post(f"/finance/v1/journal-vouchers/{jv.id}/review", headers=_h())
    r = await client.post("/finance/v1/journal-vouchers/post-batch",
                          json={"ids": [str(jv.id)]}, headers=_h())
    assert r.status_code == 200
    assert r.json()[0]["ok"] is True
