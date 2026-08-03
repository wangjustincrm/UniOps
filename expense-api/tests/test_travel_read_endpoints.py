"""Read endpoints for the TRV picker: eligible Travel Applications + user directory.

NOTE: like test_trv_travel_application_gate.py, the brief's `client` + `auth_headers`
+ `current_user_id` fixture names don't exist in this repo's tests/conftest.py.
This module defines its own module-scoped versions (mirroring test_pa_permissions.py's
`_client_for(role, user_id)` helper) so the "eligible for this user" assertions are
tied to a known user_id rather than conftest's per-client random `sub`. `auth_headers`
is a no-op here since the `client` fixture already carries the bearer token.

The `users` table is identity-owned and does not exist in `expense_test` (see
task-5-brief environment note). The directory endpoint is written defensively —
a missing/broken `users` table returns `[]` instead of 500 — mirroring the existing
best-effort `SELECT ... FROM users` pattern already used in
app/api/v1/expenses.py (actor-name resolution, wrapped in try/except). The
directory test below only asserts the endpoint is reachable and returns a list;
it cannot assert real rows without a `users` table in this DB.
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


async def test_eligible_lists_only_approved_tra_where_user_travels(
        client, auth_headers, db_session, current_user_id):
    approved = ExpenseClaim(claim_number="TRA-E-1", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="C", department_name="Ops",
        submission_date=date(2026, 8, 3), travel_destination="Calgary",
        status="approved", created_by=uuid.uuid4())
    other = ExpenseClaim(claim_number="TRA-E-2", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="C", department_name="Ops",
        submission_date=date(2026, 8, 3), status="approved", created_by=uuid.uuid4())
    db_session.add_all([approved, other]); await db_session.flush()
    db_session.add(ExpenseTraveler(claim_id=approved.id, user_id=current_user_id, user_name="Me", seq=0))
    db_session.add(ExpenseTraveler(claim_id=other.id, user_id=uuid.uuid4(), user_name="X", seq=0))
    await db_session.commit()
    r = await client.get("/api/v1/travel-applications/eligible", headers=auth_headers)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert str(approved.id) in ids and str(other.id) not in ids


async def test_eligible_excludes_unapproved_tra_even_when_traveler(
        client, auth_headers, db_session, current_user_id):
    submitted = ExpenseClaim(claim_number="TRA-E-3", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="C", department_name="Ops",
        submission_date=date(2026, 8, 3), status="submitted", created_by=uuid.uuid4())
    db_session.add(submitted); await db_session.flush()
    db_session.add(ExpenseTraveler(claim_id=submitted.id, user_id=current_user_id, user_name="Me", seq=0))
    await db_session.commit()
    r = await client.get("/api/v1/travel-applications/eligible", headers=auth_headers)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert str(submitted.id) not in ids


async def test_user_directory_returns_a_list(client, auth_headers):
    """The `users` table is identity-owned and absent from expense_test (see module
    docstring). This asserts the endpoint is reachable and defensively returns `[]`
    rather than 500ing on the missing table — production still queries the real
    `users` table (see app/api/v1/travel.py)."""
    r = await client.get("/api/v1/users/directory?q=jus", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
