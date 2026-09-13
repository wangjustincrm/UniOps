"""2026-08-13 whole-phase-review fix: GET /expenses/{id}/permissions is expense-api's
OWN copy of payment authority (previously `_CAN_PAY`, a visibility set) — it must
mirror finance-api's authoritative execution gate (`_PAY_ROLES`/`_PAY_ROLES_ASSIGNED`
in app/crud/payment_execute.py), not diverge from it. Before this fix, `payment_officer`
(the role that now executes payments) could never see can_pay=True here, and `ap_clerk`
still could — inviting a click that finance-api would 403.

Mirrors test_pa_permissions.py's payment_officer/ap_clerk coverage but for the plain
expense-claim (EXP/MIL/TRV/CFM) permissions endpoint, which has its own independent
can_pay computation in app/api/v1/expenses.py.

NOTE: like the other TRA/PA test modules, this repo's tests/conftest.py has no
`client` + `auth_headers` fixtures for non-admin roles — reuses the
`_client_for(role, user_id)` pattern.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import text

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


async def _make_approved_exp(db_session) -> ExpenseClaim:
    claim = ExpenseClaim(
        claim_number=f"EXP-TEST-{uuid.uuid4().hex[:8]}", claim_type="EXP",
        employee_id=uuid.uuid4(), employee_name="Claimant",
        department_name="Ops", submission_date=date(2026, 8, 3),
        status="approved", created_by=uuid.uuid4(),
    )
    db_session.add(claim)
    await db_session.commit()
    return claim


async def _grant_additional_role(db_session, user_id: str, role_code: str) -> None:
    await db_session.execute(text(
        "INSERT INTO role_defs (code, is_active) VALUES (:r, true) "
        "ON CONFLICT (code) DO NOTHING"), {"r": role_code})
    await db_session.execute(text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r)"),
        {"u": user_id, "r": role_code})
    await db_session.commit()


@pytest.mark.asyncio
async def test_can_pay_via_additional_payment_officer_role(db_session):
    claim = await _make_approved_exp(db_session)
    po_id = str(uuid.uuid4())
    await _grant_additional_role(db_session, po_id, "payment_officer")

    async with _client_for("requester", po_id) as po:
        resp = await po.get(f"/api/v1/expenses/{claim.id}/permissions")
    assert resp.status_code == 200
    assert resp.json()["can_pay"] is True


@pytest.mark.asyncio
async def test_ap_clerk_cannot_pay(db_session):
    claim = await _make_approved_exp(db_session)

    async with _client_for("ap_clerk", str(uuid.uuid4())) as ap_clerk:
        resp = await ap_clerk.get(f"/api/v1/expenses/{claim.id}/permissions")
    assert resp.status_code == 200
    assert resp.json()["can_pay"] is False


@pytest.mark.asyncio
async def test_finance_manager_can_still_pay(db_session):
    claim = await _make_approved_exp(db_session)

    async with _client_for("finance_manager", str(uuid.uuid4())) as fm:
        resp = await fm.get(f"/api/v1/expenses/{claim.id}/permissions")
    assert resp.status_code == 200
    assert resp.json()["can_pay"] is True
