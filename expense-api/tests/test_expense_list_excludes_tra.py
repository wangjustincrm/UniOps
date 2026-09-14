"""GET /api/v1/expenses — the Expense Claims list must not surface TRA.

Bug (prod, 2026-08-07): a Travel Application created but not yet submitted
showed up on BOTH /travel and /expenses. TRA shares the expense_claims table
with EXP/MIL/TRV, TravelApplicationsListPage filters with `type=TRA`, but
ExpenseListPage sends no type at all — so every TRA leaked into the
reimbursement list. A TRA is not a reimbursement: total_amount is 0, it never
enters the payment path (list_expenses already excludes it from pay-role
visibility and mark-paid), and its detail route is /travel/:id, not
/expenses/:id — so a row there is also a dead-end click.

Fix: `exclude_type` query param (comma-separated), sent as `exclude_type=TRA`
by the Expense Claims page. Must hold on BOTH visibility branches of
list_expenses(): the system_admin/ap_clerk branch (crud.list_claims) and the
role-based branch.

Fixture pattern mirrors test_travel_application_list.py.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

import app.db.base as db_module
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


def _ids(body: dict) -> set[str]:
    return {item["id"] for item in body["items"]}


async def _seed(employee_id: str, claim_type: str, status: str) -> str:
    async with db_module.AsyncSessionLocal() as db:
        claim = ExpenseClaim(
            claim_number=f"{claim_type}-TEST-{uuid.uuid4().hex[:8]}",
            claim_type=claim_type,
            employee_id=uuid.UUID(employee_id), employee_name="Owner",
            department_name="Ops", submission_date=date(2026, 8, 3),
            status=status, created_by=uuid.UUID(employee_id),
        )
        db.add(claim)
        await db.commit()
        return str(claim.id)


# ── role-based branch (ordinary employee viewing their own claims) ───────────

@pytest.mark.asyncio
async def test_own_draft_tra_hidden_from_expense_list():
    """The reported bug: my own unsubmitted TRA appearing under Expense Claims."""
    owner = str(uuid.uuid4())
    tra_id = await _seed(owner, "TRA", "draft")
    exp_id = await _seed(owner, "EXP", "draft")

    async with _client_for("employee", owner) as client:
        resp = await client.get("/api/v1/expenses", params={"exclude_type": "TRA"})
    assert resp.status_code == 200
    ids = _ids(resp.json())
    assert tra_id not in ids, "TRA leaked into the Expense Claims list"
    assert exp_id in ids, "exclude_type must not drop ordinary EXP claims"


@pytest.mark.asyncio
async def test_exclude_type_corrects_total_not_just_page():
    """`total` drives pagination — filtering client-side would leave it wrong."""
    owner = str(uuid.uuid4())
    await _seed(owner, "TRA", "draft")
    await _seed(owner, "EXP", "draft")

    async with _client_for("employee", owner) as client:
        resp = await client.get("/api/v1/expenses", params={"exclude_type": "TRA"})
    assert resp.json()["total"] == 1


# ── system_admin / ap_clerk branch (crud.list_claims) ───────────────────────

@pytest.mark.asyncio
async def test_admin_branch_honours_exclude_type():
    """system_admin takes the crud.list_claims path — same rule must apply."""
    owner = str(uuid.uuid4())
    tra_id = await _seed(owner, "TRA", "draft")

    async with _client_for("system_admin", str(uuid.uuid4())) as admin:
        resp = await admin.get(
            "/api/v1/expenses", params={"exclude_type": "TRA", "page_size": 100}
        )
    assert resp.status_code == 200
    assert tra_id not in _ids(resp.json())


# ── the Travel Applications page must keep working ──────────────────────────

@pytest.mark.asyncio
async def test_type_tra_still_returns_tra():
    """No exclude_type sent by /travel — TRA must still be listed there."""
    owner = str(uuid.uuid4())
    tra_id = await _seed(owner, "TRA", "draft")

    async with _client_for("employee", owner) as client:
        resp = await client.get("/api/v1/expenses", params={"type": "TRA"})
    assert resp.status_code == 200
    assert tra_id in _ids(resp.json())


@pytest.mark.asyncio
async def test_exclude_type_accepts_multiple_and_is_optional():
    owner = str(uuid.uuid4())
    tra_id = await _seed(owner, "TRA", "draft")
    mil_id = await _seed(owner, "MIL", "draft")
    exp_id = await _seed(owner, "EXP", "draft")

    async with _client_for("employee", owner) as client:
        both = await client.get("/api/v1/expenses", params={"exclude_type": "TRA,MIL"})
        none = await client.get("/api/v1/expenses")

    assert _ids(both.json()) == {exp_id}
    # Omitting exclude_type keeps the old unfiltered behaviour (other consumers).
    assert {tra_id, mil_id, exp_id} <= _ids(none.json())
