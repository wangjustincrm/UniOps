"""Who can see a document: an employee sees what they raised, an approver sees
what they approve. (Business rule, 2026-09-11.)

The rule this replaces was much wider: if the caller's role appeared ANYWHERE
in a claim type's workflow_defs, they saw every non-draft claim of that type,
company-wide. dept_manager is a populous role, so in practice every department
manager could read every expense claim in the business — requester name,
purpose and amount. It had been written to fix something real (a claim
disappeared from an approver's list the moment they approved it), but the fix
reached far past the problem, and the narrow answer was already in the same
function: approval_events.

"What they approve" has two halves, and both are needed:
  * an OPEN approve task — what is waiting for them now;
  * an approval_events row — what they have already dealt with, whose task is
    closed.

The list and the detail endpoint must agree. A list that withholds a claim the
detail endpoint will serve is decoration — anyone who can guess a URL reads it
anyway. Hence the URL-guessing tests at the bottom.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.base as db_module
from app.models.expense import ExpenseClaim
from app.models.task_mirror import TaskMirror
from tests.conftest import _client, _make_token
from tests.test_authz_scope import _restore_workflow_defs, _set_workflow_defs

# dept_manager at step 0 is what the OLD rule keyed on — with this in place, the
# previous code showed every claim below to every dept_manager in the company.
_WORKFLOW = {"exp": [
    {"id": "s0", "role": "dept_manager", "label": "Department Manager"},
    {"id": "s1", "role": "finance_bp", "label": "Finance BP"},
]}


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _claim(test_engine, *, owner: uuid.UUID, status: str = "in_review") -> uuid.UUID:
    cid = uuid.uuid4()
    async with _factory(test_engine)() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"EC-VIS-{cid.hex[:8]}", claim_type="EXP",
            employee_id=owner, employee_name="Someone Else",
            department_name="Quality Assurance", submission_date=date(2026, 9, 11),
            status=status, approval_step_idx=0, created_by=owner,
            total_amount=Decimal("1250.00"),
        ))
        await s.commit()
    return cid


async def _open_task(claim_id: uuid.UUID, *, user_id: uuid.UUID):
    async with db_module.AsyncSessionLocal() as db:
        db.add(TaskMirror(
            id=uuid.uuid4(), document_id=claim_id, document_type="exp",
            type="approve_exp", assigned_user_id=user_id, assigned_role=None,
            is_completed=False, created_at=datetime.now(timezone.utc),
        ))
        await db.commit()


async def _acted(claim_id: uuid.UUID, *, user_id: uuid.UUID):
    """An approval_events row — what approving leaves behind once the task closes."""
    async with db_module.AsyncSessionLocal() as db:
        await db.execute(text(
            "INSERT INTO approval_events "
            "(id, document_type, document_id, document_number, step_idx, action,"
            " actor_id, actor_role, created_at) "
            "VALUES (:id, 'exp', :doc, 'EC-VIS', 0, 'approve', :actor, 'dept_manager', now())"),
            {"id": uuid.uuid4(), "doc": str(claim_id), "actor": str(user_id)})
        await db.commit()


async def _list_ids(role: str, user_id: uuid.UUID) -> set[str]:
    async with _client(_make_token(role, str(user_id))) as c:
        r = await c.get("/api/v1/expenses")
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


async def _get_status(role: str, user_id: uuid.UUID, claim_id: uuid.UUID) -> int:
    async with _client(_make_token(role, str(user_id))) as c:
        return (await c.get(f"/api/v1/expenses/{claim_id}")).status_code


# ── The employee's half ───────────────────────────────────────────────────────

async def test_i_see_what_i_raised(test_engine):
    me = uuid.uuid4()
    mine = await _claim(test_engine, owner=me, status="draft")

    assert str(mine) in await _list_ids("requester", me)


async def test_i_do_not_see_a_colleagues_claim(test_engine):
    theirs = await _claim(test_engine, owner=uuid.uuid4())

    assert str(theirs) not in await _list_ids("requester", uuid.uuid4())


# ── The approver's half ───────────────────────────────────────────────────────

async def test_an_approver_sees_what_is_waiting_for_them(test_engine):
    me = uuid.uuid4()
    claim = await _claim(test_engine, owner=uuid.uuid4())
    await _open_task(claim, user_id=me)

    assert str(claim) in await _list_ids("dept_manager", me)


async def test_an_approver_still_sees_it_after_approving(test_engine):
    """The task closes when they act; approval_events is what keeps it visible.
    Losing this is what the over-wide rule was written to avoid."""
    me = uuid.uuid4()
    claim = await _claim(test_engine, owner=uuid.uuid4(), status="approved")
    await _acted(claim, user_id=me)          # no open task — they are done

    assert str(claim) in await _list_ids("dept_manager", me)


async def test_holding_the_role_is_not_holding_the_document(test_engine):
    """The whole point. Same role, same workflow step, no task on this claim."""
    previous = await _set_workflow_defs(test_engine, _WORKFLOW)
    try:
        assigned, bystander = uuid.uuid4(), uuid.uuid4()
        claim = await _claim(test_engine, owner=uuid.uuid4())
        await _open_task(claim, user_id=assigned)

        assert str(claim) in await _list_ids("dept_manager", assigned)
        assert str(claim) not in await _list_ids("dept_manager", bystander), (
            "a manager with no task on this claim must not see it — under the "
            "old workflow_defs rule they saw every claim in the company")
    finally:
        await _restore_workflow_defs(test_engine, previous)


# ── Payment is a stage, not an approval ───────────────────────────────────────

async def test_finance_sees_approved_claims_to_settle_them(test_engine):
    """Finance never approved these and has no task on them, but cannot pay
    what it cannot see."""
    claim = await _claim(test_engine, owner=uuid.uuid4(), status="approved")

    assert str(claim) in await _list_ids("finance_bp", uuid.uuid4())


async def test_finance_does_not_see_claims_still_in_approval(test_engine):
    claim = await _claim(test_engine, owner=uuid.uuid4(), status="in_review")

    assert str(claim) not in await _list_ids("finance_bp", uuid.uuid4())


async def test_admin_sees_everything(test_engine):
    claim = await _claim(test_engine, owner=uuid.uuid4())

    assert str(claim) in await _list_ids("system_admin", uuid.uuid4())


# ── The detail endpoint must enforce the same rule ────────────────────────────

async def test_a_colleague_cannot_open_it_by_url(test_engine):
    theirs = await _claim(test_engine, owner=uuid.uuid4())

    assert await _get_status("requester", uuid.uuid4(), theirs) == 403


async def test_a_manager_without_the_task_cannot_open_it_by_url(test_engine):
    """The list and the detail endpoint have to agree, or the list is only a
    filter over something everyone can still read."""
    previous = await _set_workflow_defs(test_engine, _WORKFLOW)
    try:
        claim = await _claim(test_engine, owner=uuid.uuid4())
        await _open_task(claim, user_id=uuid.uuid4())      # someone else's task

        assert await _get_status("dept_manager", uuid.uuid4(), claim) == 403
    finally:
        await _restore_workflow_defs(test_engine, previous)


async def test_the_assigned_approver_can_open_it_by_url(test_engine):
    me = uuid.uuid4()
    claim = await _claim(test_engine, owner=uuid.uuid4())
    await _open_task(claim, user_id=me)

    assert await _get_status("dept_manager", me, claim) == 200
