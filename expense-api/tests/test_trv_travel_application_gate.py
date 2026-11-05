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
        {"sub": user_id, "role": "requester", "type": "access", "exp": datetime.utcnow() + timedelta(hours=8)},
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


def _on_behalf_body(app_id, claimant_id):
    return {**_trv_body(app_id), "claimant_id": str(claimant_id)}


async def test_applicant_files_for_a_traveler_who_becomes_the_claimant(
        client, auth_headers, db_session, current_user_id):
    traveler_id = uuid.uuid4()
    tra = await _make_approved_tra(db_session, traveler_id=traveler_id)
    tra.employee_id = current_user_id
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_on_behalf_body(tra.id, traveler_id),
                          headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["employee_id"] == str(traveler_id)      # paid to the traveler
    assert body["employee_name"] == "Me"                # roster name, not the filer's
    assert body["created_by"] == str(current_user_id)   # filed by the applicant


async def test_applicant_not_on_the_roster_cannot_claim_for_themselves(
        client, auth_headers, db_session, current_user_id):
    tra = await _make_approved_tra(db_session, traveler_id=uuid.uuid4())
    tra.employee_id = current_user_id
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_trv_body(tra.id), headers=auth_headers)
    assert r.status_code == 403


async def test_applicant_cannot_name_someone_off_the_roster(
        client, auth_headers, db_session, current_user_id):
    tra = await _make_approved_tra(db_session, traveler_id=uuid.uuid4())
    tra.employee_id = current_user_id
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_on_behalf_body(tra.id, uuid.uuid4()),
                          headers=auth_headers)
    assert r.status_code == 403


async def test_a_traveler_cannot_file_for_a_fellow_traveler(
        client, auth_headers, db_session, current_user_id):
    colleague = uuid.uuid4()
    tra = await _make_approved_tra(db_session, traveler_id=current_user_id)
    db_session.add(ExpenseTraveler(claim_id=tra.id, user_id=colleague, user_name="Other", seq=1))
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_on_behalf_body(tra.id, colleague),
                          headers=auth_headers)
    assert r.status_code == 403


async def test_only_trv_can_be_filed_for_someone_else(client, auth_headers):
    r = await client.post("/api/v1/expenses", headers=auth_headers, json={
        "claim_type": "EXP", "submission_date": "2026-08-07",
        "claimant_id": str(uuid.uuid4()), "line_items": []})
    assert r.status_code == 400


async def _filed_on_behalf(client, db_session, filer_id):
    traveler_id = uuid.uuid4()
    tra = await _make_approved_tra(db_session, traveler_id=traveler_id)
    tra.employee_id = filer_id
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_on_behalf_body(tra.id, traveler_id))
    assert r.status_code == 201, r.text
    return r.json()["id"], traveler_id


async def test_filer_and_traveler_both_keep_the_claim_but_nobody_else(
        client, db_session, current_user_id):
    claim_id, traveler_id = await _filed_on_behalf(client, db_session, current_user_id)

    for c in (client, _client_for(str(traveler_id))):
        listed = await c.get("/api/v1/expenses", params={"type": "TRV"})
        assert claim_id in {i["id"] for i in listed.json()["items"]}
        assert (await c.get(f"/api/v1/expenses/{claim_id}")).status_code == 200
        perms = (await c.get(f"/api/v1/expenses/{claim_id}/permissions")).json()
        assert perms["can_edit"] is True
        assert (await c.patch(f"/api/v1/expenses/{claim_id}", json={"notes": "x"})).status_code == 200

    stranger = _client_for(str(uuid.uuid4()))
    assert (await stranger.get(f"/api/v1/expenses/{claim_id}")).status_code == 403
    assert (await stranger.patch(f"/api/v1/expenses/{claim_id}", json={"notes": "x"})).status_code == 403


async def test_filer_sees_the_returned_claim_as_a_revise_task(client, db_session, current_user_id):
    claim_id, _ = await _filed_on_behalf(client, db_session, current_user_id)
    claim = await db_session.get(ExpenseClaim, uuid.UUID(claim_id))
    claim.status = "returned"
    await db_session.commit()
    tasks = (await client.get("/api/v1/tasks")).json()
    rows = tasks["items"] if isinstance(tasks, dict) else tasks
    assert [t["task_type"] for t in rows if t["doc_id"] == claim_id] == ["revise_expense"], rows


async def test_editing_cannot_move_a_trv_to_a_tra_its_claimant_is_not_on(
        client, db_session, current_user_id):
    claim_id, traveler_id = await _filed_on_behalf(client, db_session, current_user_id)
    other = await _make_approved_tra(db_session, traveler_id=uuid.uuid4())
    await db_session.commit()
    r = await client.patch(f"/api/v1/expenses/{claim_id}",
                           json={"travel_application_id": str(other.id)})
    assert r.status_code == 403

    db_session.add(ExpenseTraveler(claim_id=other.id, user_id=traveler_id, user_name="Me", seq=1))
    await db_session.commit()
    r = await client.patch(f"/api/v1/expenses/{claim_id}",
                           json={"travel_application_id": str(other.id)})
    assert r.status_code == 200, r.text


async def test_created_by_alone_grants_nothing_on_other_claim_types(client, db_session, current_user_id):
    other = ExpenseClaim(claim_number=f"EXP-TEST-{uuid.uuid4().hex[:8]}", claim_type="EXP",
        employee_id=uuid.uuid4(), employee_name="X", department_name="Ops",
        submission_date=date(2026, 8, 3), status="draft", created_by=current_user_id)
    db_session.add(other)
    await db_session.commit()
    assert (await client.get(f"/api/v1/expenses/{other.id}")).status_code == 403
