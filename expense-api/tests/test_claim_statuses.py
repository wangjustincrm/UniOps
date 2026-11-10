"""ISS-0220 / PORTAL.md INB-002: Portal groups OA inbox rows by claim status.
Rows that arrive only through the shared task feed carry no status, so Portal
asks POST /expenses/statuses. The read gate is the claim's own."""
import uuid
from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.main import create_app
from app.models.expense import ExpenseClaim


def _client_for(role: str, user_id: str) -> AsyncClient:
    token = jwt.encode(
        {"sub": user_id, "role": role, "type": "access",
         "exp": datetime.utcnow() + timedelta(hours=8)},
        settings.jwt_secret_key, algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _claim(db_session, status: str, owner: uuid.UUID) -> ExpenseClaim:
    c = ExpenseClaim(
        claim_number=f"EXP-T220-{uuid.uuid4().hex[:8]}", claim_type="EXP",
        employee_id=owner, employee_name="Claimant", department_name="Ops",
        submission_date=date(2026, 11, 4), status=status, created_by=owner,
    )
    db_session.add(c)
    await db_session.commit()
    return c


@pytest.mark.asyncio
async def test_admin_gets_each_claim_status(db_session):
    owner = uuid.uuid4()
    sub = await _claim(db_session, "submitted", owner)
    rev = await _claim(db_session, "in_review", owner)
    async with _client_for("system_admin", str(uuid.uuid4())) as c:
        resp = await c.post("/api/v1/expenses/statuses",
                            json={"ids": [str(sub.id), str(rev.id), str(uuid.uuid4())]})
    assert resp.status_code == 200
    got = {r["id"]: r["status"] for r in resp.json()}
    assert got == {str(sub.id): "submitted", str(rev.id): "in_review"}


@pytest.mark.asyncio
async def test_claims_the_caller_cannot_read_are_left_out(db_session):
    owner = uuid.uuid4()
    mine = await _claim(db_session, "submitted", owner)
    other = await _claim(db_session, "submitted", uuid.uuid4())
    async with _client_for("requester", str(owner)) as c:
        resp = await c.post("/api/v1/expenses/statuses",
                            json={"ids": [str(mine.id), str(other.id)]})
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [str(mine.id)]
