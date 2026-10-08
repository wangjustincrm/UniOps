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
        {"sub": user_id, "role": role, "type": "access", "exp": datetime.utcnow() + timedelta(hours=8)},
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


# ── Task 2: the DELETE endpoint ──────────────────────────────────────────────

_DELETABLE = ("draft", "returned", "submitted", "in_review")


@pytest.mark.parametrize("status", _DELETABLE)
@pytest.mark.asyncio
async def test_owner_deletes_unapproved_tra(status):
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, status)
    async with _client_for("employee", owner) as client:
        resp = await client.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 204, resp.text
    assert not await _exists(claim_id)


@pytest.mark.parametrize("status", ["approved", "cancelled", "rejected", "paid"])
@pytest.mark.asyncio
async def test_cannot_delete_once_past_approval(status):
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, status)
    async with _client_for("employee", owner) as client:
        resp = await client.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 409
    assert status in resp.json()["detail"]
    assert await _exists(claim_id)

    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()


@pytest.mark.asyncio
async def test_non_tra_claim_types_are_not_deletable():
    """EXP/MIL/TRV carry budget and payment consequences — out of scope."""
    owner = str(uuid.uuid4())
    async with db_module.AsyncSessionLocal() as db:
        exp = ExpenseClaim(
            claim_number=f"EXP-TEST-{uuid.uuid4().hex[:8]}", claim_type="EXP",
            employee_id=uuid.UUID(owner), employee_name="Owner",
            department_name="Ops", submission_date=date(2026, 8, 3),
            status="draft", created_by=uuid.UUID(owner),
        )
        db.add(exp)
        await db.commit()
        claim_id = str(exp.id)

    async with _client_for("employee", owner) as client:
        resp = await client.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 409
    assert await _exists(claim_id)

    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()


@pytest.mark.asyncio
async def test_other_employee_cannot_delete():
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "draft")
    async with _client_for("employee", str(uuid.uuid4())) as stranger:
        resp = await stranger.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 403
    assert await _exists(claim_id)

    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()


@pytest.mark.asyncio
async def test_approver_cannot_delete_someone_elses_tra():
    """Approvers have return/reject — deletion is the applicant's call."""
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "submitted")
    async with _client_for("dept_manager", str(uuid.uuid4())) as approver:
        resp = await approver.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 403
    assert await _exists(claim_id)

    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()


@pytest.mark.asyncio
async def test_system_admin_can_delete_anyones_tra():
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "submitted")
    async with _client_for("system_admin", str(uuid.uuid4())) as admin:
        resp = await admin.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 204
    assert not await _exists(claim_id)


@pytest.mark.asyncio
async def test_missing_claim_is_404():
    async with _client_for("employee", str(uuid.uuid4())) as client:
        resp = await client.delete(f"/api/v1/expenses/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tra_referenced_by_a_trv_is_refused():
    """Defence, not a live path: the TRV gate requires an APPROVED TRA and
    approved is not deletable. But travel_application_id is ON DELETE SET NULL,
    so if those rules ever drift a submitted reimbursement would silently lose
    its authorization basis."""
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "draft")
    async with db_module.AsyncSessionLocal() as db:
        trv = ExpenseClaim(
            claim_number=f"TRV-TEST-{uuid.uuid4().hex[:8]}", claim_type="TRV",
            employee_id=uuid.UUID(owner), employee_name="Owner",
            department_name="Ops", submission_date=date(2026, 8, 3),
            status="submitted", created_by=uuid.UUID(owner),
            travel_application_id=uuid.UUID(claim_id),
        )
        db.add(trv)
        await db.commit()
        trv_id = str(trv.id)

    async with _client_for("employee", owner) as client:
        resp = await client.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 409
    assert await _exists(claim_id)

    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(trv_id)))
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()


# ── Task 3: can_delete surfaced to the frontend ──────────────────────────────

@pytest.mark.asyncio
async def test_list_marks_own_draft_deletable_and_others_not():
    owner = str(uuid.uuid4())
    mine = await _seed_tra(owner, "draft")
    theirs = await _seed_tra(str(uuid.uuid4()), "draft")

    async with _client_for("system_admin", str(uuid.uuid4())) as admin:
        admin_rows = {i["id"]: i for i in (
            await admin.get("/api/v1/expenses", params={"type": "TRA", "page_size": 100})
        ).json()["items"]}
    assert admin_rows[mine]["can_delete"] is True
    assert admin_rows[theirs]["can_delete"] is True, "system_admin may delete anyone's"

    async with _client_for("employee", owner) as client:
        rows = {i["id"]: i for i in (
            await client.get("/api/v1/expenses", params={"type": "TRA"})
        ).json()["items"]}
    assert rows[mine]["can_delete"] is True
    assert theirs not in rows, "a co-worker's draft is not visible at all"

    async with db_module.AsyncSessionLocal() as db:
        for cid in (mine, theirs):
            await db.delete(await db.get(ExpenseClaim, uuid.UUID(cid)))
        await db.commit()


@pytest.mark.asyncio
async def test_list_marks_approved_tra_not_deletable():
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "approved")
    async with _client_for("employee", owner) as client:
        rows = {i["id"]: i for i in (
            await client.get("/api/v1/expenses", params={"type": "TRA"})
        ).json()["items"]}
    assert rows[claim_id]["can_delete"] is False

    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()


@pytest.mark.asyncio
async def test_permissions_endpoint_reports_can_delete():
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "submitted")

    async with _client_for("employee", owner) as client:
        mine = (await client.get(f"/api/v1/expenses/{claim_id}/permissions")).json()
    assert mine["can_delete"] is True

    async with _client_for("dept_manager", str(uuid.uuid4())) as approver:
        theirs = (await approver.get(f"/api/v1/expenses/{claim_id}/permissions")).json()
    assert theirs["can_delete"] is False

    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()
