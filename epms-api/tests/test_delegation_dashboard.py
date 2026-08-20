"""Delegated work surfaces on the two remaining EPMS dashboard/list touchpoints:
the dashboard KPI counts, and the "current approval step" label used by list
views (enrich_current_step).

⚠️ epms-api does not own `approval_delegations` (it is a shadow table created
in conftest.py — see test_engine, matching test_delegation_query.py); rows are
inserted with raw SQL, matching how app.core.delegation itself reads the table.
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.delegation import local_today
from app.core.security import hash_password
from app.crud import dashboard as dash
from app.crud.current_step import enrich_current_step
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User

TAG = uuid.uuid4().hex[:8]


def _user(full_name, *, role="dept_manager", is_active=True):
    uid = uuid.uuid4()
    return User(
        id=uid, email=f"u-{uid.hex[:8]}-{TAG}@example.com",
        hashed_password=hash_password("x"), full_name=full_name,
        role=role, is_active=is_active,
    )


async def _insert_delegation(db, *, delegator_id, delegate_id,
                              start_date=None, end_date=None):
    today = local_today()
    await db.execute(text(
        "INSERT INTO approval_delegations "
        "(id, delegator_user_id, delegate_user_id, start_date, end_date, "
        " revoked_at, created_by) "
        "VALUES (:id, :delegator_id, :delegate_id, :start_date, :end_date, "
        " NULL, :created_by)"),
        {
            "id": uuid.uuid4(), "delegator_id": delegator_id,
            "delegate_id": delegate_id,
            "start_date": start_date or today - timedelta(days=1),
            "end_date": end_date or today + timedelta(days=1),
            "created_by": uuid.uuid4(),
        })


def _task(*, doc_type, task_type, assigned_user_id, assigned_role="dept_manager",
          document_id=None, due_date=None, is_completed=False):
    return Task(
        id=uuid.uuid4(), type=task_type, priority="normal",
        document_type=doc_type, document_id=document_id or uuid.uuid4(),
        document_number=f"{doc_type.upper()}-{TAG}",
        assigned_role=assigned_role, assigned_user_id=assigned_user_id,
        title="Approve", is_completed=is_completed, due_date=due_date,
    )


def _kpi(resp, title):
    return next(k.value for k in resp.kpis if k.title == title)


def _pr(*, status, created_by, document_id=None):
    return PurchaseRequest(
        id=document_id or uuid.uuid4(), number=f"PR-{TAG}-{uuid.uuid4().hex[:6]}",
        title="t", type=2, status=status, created_by=created_by,
    )


# ── 1. Pending-approval count widens for the delegate ───────────────────────

async def test_pending_approvals_count_includes_delegator_approve_tasks(test_engine):
    """Inside the delegation window, the delegate's Pending Approvals count
    includes the delegator's open approve_pr tasks (in addition to the
    delegate's own)."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator = _user("Sivers")
        delegate = _user("Mohammadi")
        db.add_all([delegator, delegate])
        await db.flush()
        await _insert_delegation(db, delegator_id=delegator.id, delegate_id=delegate.id)

        # Delegator's own open approve_pr task — must count toward the delegate.
        pr1_id = uuid.uuid4()
        db.add(_pr(status="in_review", created_by=delegator.id, document_id=pr1_id))
        db.add(_task(doc_type="pr", task_type="approve_pr", assigned_user_id=delegator.id,
                      document_id=pr1_id))
        # Delegate's own open approve_pr task — must also count (additive).
        pr2_id = uuid.uuid4()
        db.add(_pr(status="in_review", created_by=delegate.id, document_id=pr2_id))
        db.add(_task(doc_type="pr", task_type="approve_pr", assigned_user_id=delegate.id,
                      document_id=pr2_id))
        await db.commit()

        resp = await dash.build_approver(db, delegate.id, "dept_manager")
        assert _kpi(resp, "Pending Approvals") == "2"


async def test_pending_approvals_count_includes_delegator_role_pool_approve_tasks(test_engine):
    """Inside the window, the delegate's Pending Approvals count includes a
    ROLE-POOL approve_pr task assigned to a role the delegator holds (not
    directly to them) — the same widening the Task Inbox already applies via
    task.py's delegated_broadcast_roles. Before this fix the dashboard's
    _task_subq role arm only matched the VIEWER's own effective_roles, so a
    delegate covering a role-pool poster (e.g. Finance Manager) saw the task
    in their inbox but got no Pending-Approvals row for it — the two surfaces
    disagreed despite the dashboard's own docstring promising they always do."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator = _user("Sivers", role="procurement_manager")
        delegate = _user("Mohammadi", role="requester")
        db.add_all([delegator, delegate])
        await db.flush()
        await _insert_delegation(db, delegator_id=delegator.id, delegate_id=delegate.id)

        pr_id = uuid.uuid4()
        db.add(_pr(status="in_review", created_by=delegator.id, document_id=pr_id))
        db.add(_task(doc_type="pr", task_type="approve_pr", assigned_user_id=None,
                      assigned_role="procurement_manager", document_id=pr_id))
        await db.commit()

        resp = await dash.build_approver(db, delegate.id, "requester")
        assert _kpi(resp, "Pending Approvals") == "1"


async def test_pending_approvals_count_excludes_delegator_tasks_without_delegation(test_engine):
    """Without an active delegation, the delegator's open task must NOT count
    toward an unrelated user."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        delegator = _user("Sivers")
        bystander = _user("Mohammadi")
        db.add_all([delegator, bystander])
        await db.flush()
        pr_id = uuid.uuid4()
        db.add(_pr(status="in_review", created_by=delegator.id, document_id=pr_id))
        db.add(_task(doc_type="pr", task_type="approve_pr", assigned_user_id=delegator.id,
                      document_id=pr_id))
        await db.commit()

        resp = await dash.build_approver(db, bystander.id, "dept_manager")
        assert _kpi(resp, "Pending Approvals") == "0"


# ── 2. Overdue-task count widens, restricted to approve% for the delegated half ──

async def test_overdue_count_includes_delegator_overdue_approve_tasks_only(test_engine):
    """Inside the window, the delegate's Overdue Tasks count includes the
    delegator's overdue APPROVE tasks, but NOT the delegator's other overdue
    tasks (e.g. a revise_pr task) — the delegate never inherits a broadcast
    of the delegator's non-approval workload."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    yesterday = local_today() - timedelta(days=1)
    async with sf() as db:
        delegator = _user("Sivers")
        delegate = _user("Mohammadi")
        db.add_all([delegator, delegate])
        await db.flush()
        await _insert_delegation(db, delegator_id=delegator.id, delegate_id=delegate.id)

        # Delegator's overdue APPROVE task — must count.
        db.add(_task(doc_type="pr", task_type="approve_pr",
                      assigned_user_id=delegator.id, due_date=yesterday))
        # Delegator's overdue NON-approve task — must NOT count.
        db.add(_task(doc_type="pr", task_type="revise_pr",
                      assigned_user_id=delegator.id, due_date=yesterday))
        # Delegate's own overdue task (any type) — must count.
        db.add(_task(doc_type="gr", task_type="acknowledge_gr",
                      assigned_user_id=delegate.id, due_date=yesterday))
        await db.commit()

        resp = await dash.build_requester(db, delegate.id)
        assert _kpi(resp, "Overdue Tasks") == "2"


async def test_overdue_count_excludes_delegator_tasks_without_delegation(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    yesterday = local_today() - timedelta(days=1)
    async with sf() as db:
        delegator = _user("Sivers")
        bystander = _user("Mohammadi")
        db.add_all([delegator, bystander])
        await db.flush()
        db.add(_task(doc_type="pr", task_type="approve_pr",
                      assigned_user_id=delegator.id, due_date=yesterday))
        await db.commit()

        resp = await dash.build_requester(db, bystander.id)
        assert _kpi(resp, "Overdue Tasks") == "0"


# ── 3 & 4. current_step.approver_name stand-in label ─────────────────────────

class _Item:
    def __init__(self, id, status="in_review"):
        self.id = id
        self.status = status
        self.current_step = None


async def test_current_step_label_shows_stand_in_when_delegated(test_engine):
    """A list row whose open task belongs to a delegator renders
    current_step.approver_name as "Sivers (delegated: Mohammadi)"."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sf() as db:
        delegator = _user("Sivers", role="dept_manager")
        delegate = _user("Mohammadi", role="dept_manager")
        db.add_all([delegator, delegate])
        await db.flush()
        await _insert_delegation(db, delegator_id=delegator.id, delegate_id=delegate.id)
        db.add(_task(doc_type="pr", task_type="approve_pr", assigned_user_id=delegator.id,
                      assigned_role="dept_manager", document_id=doc_id))
        await db.commit()

        items = [_Item(doc_id)]
        await enrich_current_step(db, "pr", items)

    assert items[0].current_step["approver_name"] == "Sivers (delegated: Mohammadi)"


async def test_current_step_label_plain_without_delegation(test_engine):
    """With no delegation active, the label is unchanged: "Sivers"."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sf() as db:
        approver = _user("Sivers", role="dept_manager")
        db.add(approver)
        await db.flush()
        db.add(_task(doc_type="pr", task_type="approve_pr", assigned_user_id=approver.id,
                      assigned_role="dept_manager", document_id=doc_id))
        await db.commit()

        items = [_Item(doc_id)]
        await enrich_current_step(db, "pr", items)

    assert items[0].current_step["approver_name"] == "Sivers"
