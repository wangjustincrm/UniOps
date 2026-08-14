"""GET /expenses/my-actions is scoped by the tasks table, not by role alone.

The approval half of the Portal task inbox used to select every claim sitting at
a workflow step whose role the caller holds — with no department predicate and
no look at who the claim is actually assigned to. Because `dept_manager` is a
populous role, any department manager saw every company claim parked at that
step (requester name + amount included). Confirmed on a production snapshot:
Lauren Cox (Sales) saw Etienne Clement's Quality Assurance claims.

approval-api already answers this exactly: it PINS a dept_manager approval task
to the one manager who routes for that claim's department (engine.py refuses to
create the task at all if it cannot resolve one), and broadcasts by role only
for role pools and singleton posts. Reading the tasks table therefore gives the
department scope for free and makes the inbox agree, row for row, with
`_can_act_on_claim` — the gate that decides whether the Approve button works.

The payment branch (approved claims for _CAN_PAY roles) is a role pool with no
per-document task, and stays role-based.
"""
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.base as db_module
from app.models.expense import ExpenseClaim
from app.models.task_mirror import TaskMirror
from tests.conftest import _client, _make_token

MY_ACTIONS = "/api/v1/expenses/my-actions"


async def _seed_claim(test_engine, *, claim_type: str = "EXP", status: str = "in_review",
                      step_idx: int = 0, employee_id: uuid.UUID | None = None) -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    emp = employee_id or uuid.uuid4()
    async with factory() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"EC-TS-{cid.hex[:8]}", claim_type=claim_type,
            employee_id=emp, employee_name="Other Department Employee",
            submission_date=date(2026, 8, 14), status=status,
            approval_step_idx=step_idx, created_by=emp,
        ))
        await s.commit()
    return cid


async def _add_task(claim_id: uuid.UUID, *, user_id: str | None = None,
                    role: str | None = None, completed: bool = False,
                    task_type: str = "approve_exp") -> None:
    async with db_module.AsyncSessionLocal() as db:
        db.add(TaskMirror(
            id=uuid.uuid4(), document_id=claim_id, document_type="exp",
            type=task_type,
            assigned_user_id=uuid.UUID(user_id) if user_id else None,
            assigned_role=role, is_completed=completed,
            created_at=datetime.now(timezone.utc),
        ))
        await db.commit()


async def _grant_additional_role(user_id: str, role_code: str) -> None:
    async with db_module.AsyncSessionLocal() as db:
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r) "
            "ON CONFLICT DO NOTHING"), {"u": user_id, "r": role_code})
        await db.commit()


async def _inbox_ids(role: str, user_id: str) -> set[str]:
    async with _client(_make_token(role, user_id)) as c:
        r = await c.get(MY_ACTIONS)
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


@pytest.mark.asyncio
async def test_claim_pinned_to_me_is_in_my_inbox(test_engine):
    me = str(uuid.uuid4())
    cid = await _seed_claim(test_engine)
    await _add_task(cid, user_id=me)
    assert str(cid) in await _inbox_ids("dept_manager", me)


@pytest.mark.asyncio
async def test_claim_pinned_to_another_manager_is_not_in_my_inbox(test_engine):
    """THE BUG: both users are dept_manager and the claim sits at the
    dept_manager step, but the task belongs to the other department's manager."""
    me, other = str(uuid.uuid4()), str(uuid.uuid4())
    cid = await _seed_claim(test_engine)
    await _add_task(cid, user_id=other)
    assert str(cid) not in await _inbox_ids("dept_manager", me)
    assert str(cid) in await _inbox_ids("dept_manager", other)


@pytest.mark.asyncio
async def test_role_broadcast_task_reaches_every_holder(test_engine):
    """Role pools / singleton posts are broadcast (assigned_user_id NULL) so the
    current holder resolves live — those must still reach the whole pool."""
    me = str(uuid.uuid4())
    cid = await _seed_claim(test_engine)
    await _add_task(cid, role="finance_bp")
    assert str(cid) in await _inbox_ids("finance_bp", me)
    assert str(cid) not in await _inbox_ids("dept_manager", str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_gm_or_opm_synthetic_role_is_resolved(test_engine):
    """`gm_or_opm` is not a real role code — a gm OR an opm satisfies it."""
    cid = await _seed_claim(test_engine)
    await _add_task(cid, role="gm_or_opm")
    assert str(cid) in await _inbox_ids("gm", str(uuid.uuid4()))
    assert str(cid) in await _inbox_ids("opm", str(uuid.uuid4()))
    assert str(cid) not in await _inbox_ids("requester", str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_additional_role_counts_for_broadcast_tasks(test_engine):
    me = str(uuid.uuid4())
    await _grant_additional_role(me, "finance_bp")
    cid = await _seed_claim(test_engine)
    await _add_task(cid, role="finance_bp")
    assert str(cid) in await _inbox_ids("requester", me)


@pytest.mark.asyncio
async def test_completed_task_leaves_the_inbox(test_engine):
    """A stranded claim — status still in_review but its approval task was closed
    (the Mark Done incident) — is no longer offered as an action. Nobody can
    approve it; it needs the heal script, not an inbox row."""
    me = str(uuid.uuid4())
    cid = await _seed_claim(test_engine)
    await _add_task(cid, user_id=me, completed=True)
    assert str(cid) not in await _inbox_ids("dept_manager", me)


@pytest.mark.asyncio
async def test_draft_claim_never_appears(test_engine):
    me = str(uuid.uuid4())
    cid = await _seed_claim(test_engine, status="draft")
    await _add_task(cid, user_id=me)
    assert str(cid) not in await _inbox_ids("dept_manager", me)


@pytest.mark.asyncio
async def test_payment_branch_still_role_based(test_engine):
    """Approved claims waiting to be paid have no per-document task — that branch
    is a role pool and must keep working."""
    cid = await _seed_claim(test_engine, status="approved")
    ids = await _inbox_ids("ap_clerk", str(uuid.uuid4()))
    assert str(cid) in ids


@pytest.mark.asyncio
async def test_approved_travel_application_still_excluded_from_payment_branch(test_engine):
    cid = await _seed_claim(test_engine, claim_type="TRA", status="approved")
    assert str(cid) not in await _inbox_ids("ap_clerk", str(uuid.uuid4()))
