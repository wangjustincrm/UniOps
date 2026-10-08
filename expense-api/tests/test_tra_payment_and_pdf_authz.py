"""Final-fix-wave regression tests for TRA (Travel Application):

I-2: an approved TRA has total_amount 0 and must never enter the payment path —
POST /expenses/{id}/pay must 409, and GET /expenses/{id}/permissions must report
can_pay=false, regardless of the caller's role.

I-3: POST /travel-applications/{id}/pdf must reject callers who are neither the
claim owner, an approver of the claim, nor a finance/admin role. Only the authz
gate is tested here (it runs before any file-server upload) — the success path
needs file-api and is out of scope for this suite.

NOTE: like the other TRA test modules (test_trv_travel_application_gate.py,
test_travel_application_list.py), this repo's tests/conftest.py has no
`client` + `auth_headers` fixtures for non-admin roles — reuses the
`_client_for(role, user_id)` pattern and unique `TRA-TEST-{uuid}` claim numbers.
"""
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
        {"sub": user_id, "role": role, "type": "access", "exp": datetime.utcnow() + timedelta(hours=8)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _make_approved_tra(db_session, employee_id=None) -> ExpenseClaim:
    tra = ExpenseClaim(
        claim_number=f"TRA-TEST-{uuid.uuid4().hex[:8]}", claim_type="TRA",
        employee_id=employee_id or uuid.uuid4(), employee_name="Traveler",
        department_name="Ops", submission_date=date(2026, 8, 3),
        status="approved", created_by=uuid.uuid4(),
    )
    db_session.add(tra)
    await db_session.commit()
    return tra


# ── I-2: payment path exclusion ─────────────────────────────────────────────

async def test_pay_approved_tra_returns_409(admin_client, db_session):
    tra = await _make_approved_tra(db_session)
    r = await admin_client.post(
        f"/api/v1/expenses/{tra.id}/pay",
        json={"bank_account_id": str(uuid.uuid4())},
    )
    assert r.status_code == 409, r.text
    assert "not payable" in r.json()["detail"].lower()


async def test_permissions_approved_tra_can_pay_false(admin_client, db_session):
    tra = await _make_approved_tra(db_session)
    r = await admin_client.get(f"/api/v1/expenses/{tra.id}/permissions")
    assert r.status_code == 200, r.text
    assert r.json()["can_pay"] is False


# ── I-3: regenerate-PDF document-level authz ────────────────────────────────

async def test_regenerate_pdf_forbidden_for_unrelated_user(db_session):
    owner_id = uuid.uuid4()
    tra = await _make_approved_tra(db_session, employee_id=owner_id)

    stranger_id = str(uuid.uuid4())
    async with _client_for("requester", stranger_id) as stranger:
        r = await stranger.post(f"/api/v1/travel-applications/{tra.id}/pdf")
    assert r.status_code == 403, r.text
    assert "not authorized" in r.json()["detail"].lower()
