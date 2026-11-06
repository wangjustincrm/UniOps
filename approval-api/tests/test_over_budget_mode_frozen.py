"""The over-budget mode is frozen on a PR at submission; Budget Config changes
afterwards only ever LOOSEN an in-flight PR, by waiving steps, never by
rebuilding its step list.

Incident 2026-10-16 (ISS-0006): Budget Config moved fm_only → allow. The engine
rebuilt every PR's steps from the live mode, so "Over-Budget — Finance Manager"
vanished from all submitted over-budget PRs: PR-20261015-0003's timeline put its
approvers under the wrong roles, and five PRs still waiting on the Finance
Manager became unapprovable (step 0 now meant Department Manager → her task
403'd; the Department Manager had no task at all).
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt
from sqlalchemy import select

from app.core.config import settings
from app.crud.engine import build_effective_workflow, execute_action
from app.db.base import get_db
from app.main import app
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User

pytestmark = pytest.mark.asyncio
BASE = "/approval/v1"
PR_FLOW = [{"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
           {"id": "director", "role": "director", "label": "Director"}]


@pytest.fixture(autouse=True)
def _override_get_db(engine_db_session):
    async def _get_db_override():
        yield engine_db_session
    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(get_db, None)


def _client(role):
    token = jose_jwt.encode({"sub": str(uuid.uuid4()), "role": role},
                            settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _world(db, mode: str):
    """Department with a manager (no Director), a Finance Manager, a requester,
    and Budget Config at `mode`. Returns (cfg, requester, fm, dm)."""
    dept_id = uuid.uuid4()
    await db.execute(sa.text("DROP TABLE IF EXISTS departments CASCADE"))
    await db.execute(sa.text(
        "CREATE TABLE departments (id uuid PRIMARY KEY, code varchar(50),"
        " name varchar(255), is_active boolean NOT NULL DEFAULT true)"))
    await db.execute(sa.text(
        "INSERT INTO departments (id, code, name, is_active) VALUES (:i,'OBF','OB Dept',true)"),
        {"i": str(dept_id)})
    requester = User(full_name="Requester", id=uuid.uuid4(), role="requester",
                     department_id=dept_id, is_active=True)
    fm = User(full_name="Finance Manager", id=uuid.uuid4(), role="finance_manager",
              department_id=uuid.uuid4(), is_active=True)
    dm = User(full_name="Dept Manager", id=uuid.uuid4(), role="dept_manager",
              department_id=dept_id, is_active=True)
    cfg = CompanyConfig(id=uuid.uuid4(), workflow_defs={"pr": PR_FLOW},
                        budget_admin_config={"over_budget_mode": mode})
    db.add_all([requester, fm, dm, cfg])
    await db.flush()
    return cfg, requester, fm, dm


async def _submit_ob_pr(db, requester) -> PurchaseRequest:
    pr = PurchaseRequest(number=f"PR-OBF-{uuid.uuid4().hex[:4]}", title="Over budget",
                         status="draft", approval_step_idx=0, amount=Decimal("3250.00"),
                         created_by=requester.id, over_budget=True,
                         over_budget_justification="urgent")
    db.add(pr)
    await db.flush()
    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")
    await db.refresh(pr)
    return pr


async def _set_mode(db, cfg, mode):
    cfg.budget_admin_config = {"over_budget_mode": mode}
    await db.flush()


async def _open_tasks(db, pr):
    return (await db.execute(select(Task).where(
        Task.document_id == pr.id, Task.type == "approve_pr",
        Task.is_completed.is_(False)))).scalars().all()


async def _ids(db, pr, cfg):
    return [n["id"] for n in await build_effective_workflow(db, "pr", pr, cfg)]


async def test_submit_freezes_mode_and_later_change_keeps_history_aligned(engine_db_session):
    """PR-20261015-0003: approved under fm_only, then Budget Config → allow.
    Its step list (what the timeline renders) must not lose the FM step."""
    db = engine_db_session
    cfg, requester, fm, dm = await _world(db, "fm_only")
    pr = await _submit_ob_pr(db, requester)
    assert pr.ob_mode_applied == "fm_only"
    await execute_action(db, "pr", pr.id, "approve", fm.id, "finance_manager")
    await execute_action(db, "pr", pr.id, "approve", dm.id, "dept_manager")
    await db.refresh(pr)
    assert pr.status == "approved"

    await _set_mode(db, cfg, "allow")
    assert await _ids(db, pr, cfg) == ["ob_finance_manager", "dept_manager", "director"]
    # …and every recorded approval still sits on the node it was given at.
    steps = await build_effective_workflow(db, "pr", pr, cfg)
    events = (await db.execute(select(ApprovalEvent).where(
        ApprovalEvent.document_id == pr.id, ApprovalEvent.action == "approve"))).scalars().all()
    by_idx = {e.step_idx: e.actor_role for e in events}
    assert by_idx[0] == "finance_manager" and steps[0]["role"] == "finance_manager"
    assert by_idx[1] == "dept_manager" and steps[1]["role"] == "dept_manager"


async def test_loosening_to_allow_moves_the_fm_task_on(engine_db_session):
    """The five stranded PRs: waiting on the FM when the mode became allow. The
    Budget Config save (→ resync-over-budget) must close the FM's task and hand
    the PR to the Department Manager, recording the waiver at the FM step."""
    db = engine_db_session
    cfg, requester, fm, dm = await _world(db, "fm_only")
    pr = await _submit_ob_pr(db, requester)
    [t] = await _open_tasks(db, pr)
    assert t.assigned_role == "finance_manager"

    await _set_mode(db, cfg, "allow")
    # Saved by a Finance Manager (admin_panel holder, not system_admin) — must not 403.
    async with _client("finance_manager") as c:
        r = await c.post(f"{BASE}/routing/resync-over-budget")
    assert r.status_code == 200, r.text

    await db.refresh(pr)
    [t] = await _open_tasks(db, pr)
    assert (t.assigned_role, t.assigned_user_id) == ("dept_manager", dm.id)
    assert pr.approval_step_idx == 1
    assert pr.ob_mode_applied == "fm_only"   # history still says what it was submitted under
    waiver = (await db.execute(select(ApprovalEvent).where(
        ApprovalEvent.document_id == pr.id, ApprovalEvent.step_idx == 0,
        ApprovalEvent.action == "approve"))).scalar_one()
    assert "Auto-skipped" in waiver.comment and "Allow" in waiver.comment

    # The Department Manager can now finish it; the Director level auto-skips.
    await execute_action(db, "pr", pr.id, "approve", dm.id, "dept_manager")
    await db.refresh(pr)
    assert pr.status == "approved"


async def test_fm_can_still_approve_her_step_before_the_resync(engine_db_session):
    """Positive control for the test above: before the resync, the FM's approval
    works (her step is still current) — the waiver is what moves it, not a
    rebuilt step list making her step disappear."""
    db = engine_db_session
    cfg, requester, fm, dm = await _world(db, "fm_only")
    pr = await _submit_ob_pr(db, requester)
    await _set_mode(db, cfg, "allow")
    # No resync yet: the FM can still act on the step she holds (it used to 403).
    await execute_action(db, "pr", pr.id, "approve", fm.id, "finance_manager")
    await db.refresh(pr)
    [t] = await _open_tasks(db, pr)
    assert t.assigned_role == "dept_manager" and pr.approval_step_idx == 1


async def test_tightening_never_adds_steps_to_a_submitted_pr(engine_db_session):
    db = engine_db_session
    cfg, requester, fm, dm = await _world(db, "allow")
    pr = await _submit_ob_pr(db, requester)
    assert pr.ob_mode_applied == "allow"
    await _set_mode(db, cfg, "fm_only")
    assert await _ids(db, pr, cfg) == ["dept_manager", "director"]
    async with _client("finance_manager") as c:
        assert (await c.post(f"{BASE}/routing/resync-over-budget")).status_code == 200
    [t] = await _open_tasks(db, pr)
    assert t.assigned_role == "dept_manager"


async def test_fm_gm_opm_to_fm_only_waives_the_pending_gm_step_on_approve(engine_db_session):
    db = engine_db_session
    cfg, requester, fm, dm = await _world(db, "fm_gm_opm")
    pr = await _submit_ob_pr(db, requester)
    assert await _ids(db, pr, cfg) == ["ob_finance_manager", "ob_gm_or_opm",
                                       "dept_manager", "director"]
    await _set_mode(db, cfg, "fm_only")
    await execute_action(db, "pr", pr.id, "approve", fm.id, "finance_manager")
    await db.refresh(pr)
    [t] = await _open_tasks(db, pr)
    assert t.assigned_role == "dept_manager" and pr.approval_step_idx == 2
    gm_evt = (await db.execute(select(ApprovalEvent).where(
        ApprovalEvent.document_id == pr.id, ApprovalEvent.step_idx == 1))).scalar_one()
    assert "Auto-skipped" in gm_evt.comment


async def test_hard_block_does_not_waive_in_flight_steps(engine_db_session):
    """hard_block forbids over-budget spend; reading it as "no FM step needed"
    would wave in-flight over-budget PRs through."""
    db = engine_db_session
    cfg, requester, fm, dm = await _world(db, "fm_only")
    pr = await _submit_ob_pr(db, requester)
    await _set_mode(db, cfg, "hard_block")
    async with _client("finance_manager") as c:
        assert (await c.post(f"{BASE}/routing/resync-over-budget")).status_code == 200
    [t] = await _open_tasks(db, pr)
    assert t.assigned_role == "finance_manager"


async def test_resubmission_keeps_frozen_mode_and_honours_a_looser_one(engine_db_session):
    db = engine_db_session
    cfg, requester, fm, dm = await _world(db, "fm_only")
    pr = await _submit_ob_pr(db, requester)
    await execute_action(db, "pr", pr.id, "return", fm.id, "finance_manager", comment="fix")
    await _set_mode(db, cfg, "allow")
    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")
    await db.refresh(pr)
    assert pr.ob_mode_applied == "fm_only"
    assert await _ids(db, pr, cfg) == ["ob_finance_manager", "dept_manager", "director"]
    [t] = await _open_tasks(db, pr)
    assert t.assigned_role == "dept_manager" and pr.approval_step_idx == 1


async def test_resubmission_after_leaving_over_budget_restarts_on_plain_flow(engine_db_session):
    db = engine_db_session
    cfg, requester, fm, dm = await _world(db, "fm_only")
    pr = await _submit_ob_pr(db, requester)
    await execute_action(db, "pr", pr.id, "approve", fm.id, "finance_manager")
    await execute_action(db, "pr", pr.id, "return", dm.id, "dept_manager", comment="cut it")
    pr.over_budget = False          # requester trimmed the amount under budget
    await db.flush()
    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")
    await db.refresh(pr)
    assert pr.ob_mode_applied == "none"
    assert await _ids(db, pr, cfg) == ["dept_manager", "director"]
    [t] = await _open_tasks(db, pr)
    assert t.assigned_role == "dept_manager" and pr.approval_step_idx == 0


async def test_draft_shows_live_mode_even_after_a_recall(engine_db_session):
    db = engine_db_session
    cfg, requester, fm, dm = await _world(db, "fm_only")
    pr = await _submit_ob_pr(db, requester)
    await execute_action(db, "pr", pr.id, "recall", requester.id, "requester")
    await db.refresh(pr)
    assert pr.status == "draft"
    await _set_mode(db, cfg, "allow")
    assert await _ids(db, pr, cfg) == ["dept_manager", "director"]
