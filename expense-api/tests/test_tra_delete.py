"""DELETE /api/v1/expenses/{id} — hard-delete an unapproved Travel Application.

TRA shares expense_claims with EXP/MIL/TRV but is a pre-trip authorization, not
a reimbursement. An application raised by mistake had no way out: recall/cancel
exist in the approval engine but are not exposed in OA, so it sat in the list
and kept an approve task alive in approvers' inboxes.

`tasks` and `approval_events` are approval-api's polymorphic tables keyed by
document_id with NO foreign key to expense_claims — deleting the claim alone
leaves orphaned approve tasks. Data Maintenance already solved this
(app/admin/registry.py::_claim_delete); the delete path is shared rather than
duplicated.

Fixture pattern mirrors test_travel_application_list.py.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import func, select

import app.db.base as db_module
from app.core.config import settings
from app.main import create_app
from app.models.approval_event_mirror import ApprovalEventMirror
from app.models.expense import ExpenseClaim, ExpenseTraveler
from app.models.task_mirror import TaskMirror


def _client_for(role: str, user_id: str) -> AsyncClient:
    token = jwt.encode(
        {"sub": user_id, "role": role, "exp": datetime.utcnow() + timedelta(hours=8)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _seed_tra(owner_id: str, status: str = "draft", *, travelers: bool = False) -> str:
    async with db_module.AsyncSessionLocal() as db:
        tra = ExpenseClaim(
            claim_number=f"TRA-TEST-{uuid.uuid4().hex[:8]}", claim_type="TRA",
            employee_id=uuid.UUID(owner_id), employee_name="Owner",
            department_name="Ops", submission_date=date(2026, 8, 3),
            status=status, created_by=uuid.UUID(owner_id),
        )
        db.add(tra)
        await db.flush()
        if travelers:
            db.add(ExpenseTraveler(claim_id=tra.id, user_id=uuid.UUID(owner_id), user_name="Owner"))
        await db.commit()
        return str(tra.id)


async def _count(model, claim_id: str) -> int:
    async with db_module.AsyncSessionLocal() as db:
        return int((await db.execute(
            select(func.count()).select_from(model)
            .where(model.document_id == uuid.UUID(claim_id))
        )).scalar_one())


async def _exists(claim_id: str) -> bool:
    async with db_module.AsyncSessionLocal() as db:
        return (await db.get(ExpenseClaim, uuid.UUID(claim_id))) is not None


# ── Task 1: shared delete path ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_claim_purges_shared_refs_and_children():
    """crud.delete_claim removes the claim, its FK children, and the polymorphic
    tasks/approval_events rows that have no FK to expense_claims."""
    from app.crud import expense as expense_crud

    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "submitted", travelers=True)

    async with db_module.AsyncSessionLocal() as db:
        # TaskMirror.id has no default (the table is written by approval-api) —
        # it must be supplied explicitly. Columns per app/models/task_mirror.py:
        # `type`/`is_completed`, not task_type/status.
        db.add(TaskMirror(
            id=uuid.uuid4(), document_type="tra", document_id=uuid.UUID(claim_id),
            type="approve_tra", assigned_role="dept_manager", is_completed=False,
        ))
        # document_number and actor_role are NOT NULL on approval_events.
        db.add(ApprovalEventMirror(
            document_type="tra", document_id=uuid.UUID(claim_id),
            document_number="TRA-TEST", step_idx=0, action="submit",
            actor_id=uuid.UUID(owner), actor_role="employee",
        ))
        await db.commit()

    assert await _count(TaskMirror, claim_id) == 1
    assert await _count(ApprovalEventMirror, claim_id) == 1

    async with db_module.AsyncSessionLocal() as db:
        claim = await db.get(ExpenseClaim, uuid.UUID(claim_id))
        summary = await expense_crud.delete_claim(db, claim)
        await db.commit()

    assert summary == {"expense_claims": 1, "tasks": 1, "approval_events": 1}
    assert not await _exists(claim_id)
    assert await _count(TaskMirror, claim_id) == 0
    assert await _count(ApprovalEventMirror, claim_id) == 0

    async with db_module.AsyncSessionLocal() as db:
        travelers = int((await db.execute(
            select(func.count()).select_from(ExpenseTraveler)
            .where(ExpenseTraveler.claim_id == uuid.UUID(claim_id))
        )).scalar_one())
    assert travelers == 0, "expense_travelers should cascade via FK"
