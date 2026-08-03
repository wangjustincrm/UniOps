"""TRV reimbursement gate: creating a TRV claim requires `travel_application_id`
pointing at an APPROVED TRA on which the current user is a listed traveler.

NOTE: like test_travel_application_create.py, the brief's `client` + `auth_headers`
+ `current_user_id` fixture names don't exist in this repo's tests/conftest.py.
This module defines its own module-scoped versions (mirroring test_pa_permissions.py's
`_client_for(role, user_id)` helper) so the "user is/isn't a traveler" assertions are
tied to a known user_id rather than conftest's per-client random `sub`. `auth_headers`
is a no-op here since the `client` fixture already carries the bearer token.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.main import create_app
from app.models.expense import ExpenseClaim, ExpenseTraveler


def _client_for(user_id: str) -> AsyncClient:
    token = jwt.encode(
        {"sub": user_id, "role": "requester", "exp": datetime.utcnow() + timedelta(hours=8)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
def current_user_id():
    return uuid.uuid4()


@pytest.fixture
async def client(current_user_id):
    async with _client_for(str(current_user_id)) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {}


async def _make_approved_tra(db_session, traveler_id):
    tra = ExpenseClaim(
        claim_number=f"TRA-TEST-{uuid.uuid4().hex[:8]}", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="Creator", department_name="Ops",
        submission_date=date(2026, 8, 3), status="approved", created_by=uuid.uuid4())
    db_session.add(tra)
    await db_session.flush()
    db_session.add(ExpenseTraveler(claim_id=tra.id, user_id=traveler_id, user_name="Me", seq=0))
    await db_session.flush()
    return tra


def _trv_body(app_id=None):
    return {"claim_type": "TRV", "submission_date": "2026-08-07",
            "travel_application_id": str(app_id) if app_id else None, "line_items": []}


async def test_trv_without_application_rejected(client, auth_headers):
    r = await client.post("/api/v1/expenses", json=_trv_body(None), headers=auth_headers)
    assert r.status_code == 400


async def test_trv_with_unapproved_application_rejected(client, auth_headers, db_session, current_user_id):
    tra = ExpenseClaim(claim_number=f"TRA-TEST-{uuid.uuid4().hex[:8]}", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="C", department_name="Ops",
        submission_date=date(2026, 8, 3), status="submitted", created_by=uuid.uuid4())
    db_session.add(tra)
    await db_session.flush()
    db_session.add(ExpenseTraveler(claim_id=tra.id, user_id=current_user_id, user_name="Me", seq=0))
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_trv_body(tra.id), headers=auth_headers)
    assert r.status_code == 400


async def test_trv_when_user_not_traveler_forbidden(client, auth_headers, db_session):
    tra = await _make_approved_tra(db_session, traveler_id=uuid.uuid4())  # someone else
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_trv_body(tra.id), headers=auth_headers)
    assert r.status_code == 403


async def test_trv_valid_application_allows_create(client, auth_headers, db_session, current_user_id):
    tra = await _make_approved_tra(db_session, traveler_id=current_user_id)
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_trv_body(tra.id), headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["travel_application_id"] == str(tra.id)
