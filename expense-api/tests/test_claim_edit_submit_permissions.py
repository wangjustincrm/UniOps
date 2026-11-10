"""ISS-0221: /permissions answers Edit and Submit separately, with the same
rule the endpoints enforce (OA.md §7.3).

Save / Edit: claimant (or TRV preparer); System Admin or Finance Manager by
PRIMARY role. Submit: claimant or creator; System Admin by PRIMARY role.
TRV-20261103-0002 shape: a System Admin opening TEST Requester's draft got
neither button although both endpoints accept them.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.models.expense import ExpenseClaim
from tests.conftest import _client, _make_token


async def _draft(db_session) -> ExpenseClaim:
    owner = uuid.uuid4()
    claim = ExpenseClaim(
        claim_number=f"EXP-T221-{uuid.uuid4().hex[:6]}", claim_type="EXP",
        employee_id=owner, employee_name="TEST Requester", department_name="Ops",
        submission_date=date(2026, 11, 3), status="draft", created_by=owner,
    )
    db_session.add(claim)
    await db_session.commit()
    return claim


async def _perms(claim, role, user_id=None) -> dict:
    async with _client(_make_token(role, str(user_id or uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/expenses/{claim.id}/permissions")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("role, can_edit, can_submit", [
    ("system_admin", True, True),
    ("finance_manager", True, False),
    ("requester", False, False),
])
async def test_other_users_draft(db_session, role, can_edit, can_submit):
    p = await _perms(await _draft(db_session), role)
    assert (p["can_edit"], p["can_submit"]) == (can_edit, can_submit)


@pytest.mark.asyncio
async def test_owner_may_edit_and_submit(db_session):
    claim = await _draft(db_session)
    p = await _perms(claim, "requester", claim.employee_id)
    assert (p["can_edit"], p["can_submit"]) == (True, True)


@pytest.mark.asyncio
async def test_additional_finance_manager_role_does_not_edit(db_session):
    """Primary role only, as update_expense checks it."""
    claim = await _draft(db_session)
    uid = uuid.uuid4()
    await db_session.execute(text(
        "INSERT INTO role_defs (code, is_active) VALUES ('finance_manager', true) "
        "ON CONFLICT (code) DO NOTHING"))
    await db_session.execute(text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_manager')"), {"u": uid})
    await db_session.commit()
    p = await _perms(claim, "requester", uid)
    assert (p["can_edit"], p["can_submit"]) == (False, False)


@pytest.mark.asyncio
async def test_the_buttons_match_what_the_endpoints_do(db_session):
    """A shown button is one the server honours: Finance Manager's edit goes
    through, their submit is refused; System Admin's submit is not refused by
    the owner gate (it fails later on the missing receipt, EXP-007)."""
    claim = await _draft(db_session)
    async with _client(_make_token("finance_manager")) as fm:
        r = await fm.patch(f"/api/v1/expenses/{claim.id}", json={"notes": "fixed"})
        assert r.status_code == 200, r.text
        r = await fm.post(f"/api/v1/expenses/{claim.id}/action", json={"action": "submit"})
        assert r.status_code == 403
    async with _client(_make_token("system_admin")) as admin:
        r = await admin.post(f"/api/v1/expenses/{claim.id}/action", json={"action": "submit"})
        assert r.status_code == 409 and "receipt" in r.json()["detail"]
