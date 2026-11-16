"""Editing an approval workflow carries in-flight documents over to the new chain.

Incident 2026-11-16: the PA (Direct) chain had its Finance Manager and Finance BP
steps swapped. Documents store only an index into the chain, so 27 PAs waiting
on the Finance Manager were suddenly "at" the Finance BP step while their task
stayed with the Finance Manager (who could no longer approve it), and every
approval line showed each approver under the other one's step.

Company rules (2026-11-16) pinned here:
  1. a step ADDED at a point the document already passed is waived, not sent back for;
  2. a step already approved in this run is not asked again, wherever it moved;
  3. when every step of the new chain is satisfied, the document is approved.
A step that existed before but has not been given yet stays required even when
it moves in front of steps already given — that is what the incident needed.
"""
import copy
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt
from sqlalchemy import select

from app.core.config import settings
from app.crud.engine import execute_action
from app.crud.workflow_realign import (WAIVED_REASON, match_nodes, plan_realign,
                                       realign_after_workflow_change, resolve_nodes)
from app.db.base import get_db
from app.main import app
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.routing import WorkflowSnapshot
from app.models.task import Task
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _n(id_, role, label=None):
    return {"id": id_, "role": role, "label": label or role}


PM, GM, FM, VM, BP = (_n("pm", "procurement_manager"), _n("gm", "gm"),
                      _n("fm", "finance_manager"), _n("vm", "vendor_manager"),
                      _n("bp", "finance_bp"))
OLD = [PM, GM, FM, VM, BP]
SWAPPED = [PM, GM, BP, VM, FM]          # the 2026-11-16 edit, same shape


# ── Pure planning ────────────────────────────────────────────────────────────

def test_match_pairs_moved_steps_by_id():
    assert match_nodes(OLD, SWAPPED) == {0: 0, 1: 1, 2: 4, 3: 3, 4: 2}


def test_match_role_changed_in_place_is_a_different_step():
    """The editor keeps a step's id when its role is changed (prod: pa_dir's
    first step is still id 'finance_mgr' with role dept_manager). Same id, other
    role = another approver — must not pair."""
    new = [_n("fm", "dept_manager"), GM]
    assert match_nodes([FM, GM], new) == {0: None, 1: 1}


def test_match_falls_back_to_role_in_order():
    new = [_n("x1", "gm"), _n("x2", "finance_manager")]
    assert match_nodes([FM, GM], new) == {0: 1, 1: 0}


def test_plan_swap_waiting_on_moved_step():
    """Waiting on FM (old step 2): PM, GM given. BP moved in front and was never
    given → required. FM moved behind → still owed."""
    p = plan_realign(OLD, SWAPPED, {0, 1})
    assert p.satisfied == {0, 1} and p.waived == []


def test_plan_added_step_behind_the_document_is_waived_ahead_is_not():
    new = [PM, _n("new1", "opm"), GM, FM, _n("new2", "gm_or_opm"), VM, BP]
    p = plan_realign(OLD, new, {0, 1, 2})          # waiting on VM
    assert p.added == [1, 4]
    assert p.waived == [1]                          # GM/FM (given) come after it
    assert 4 not in p.waived                        # nothing given after it yet


def test_resolve_nodes_falls_back_like_the_engine():
    assert resolve_nodes({}, "pa_dir")[0]["role"] == "finance_bp"   # shipped default
    assert resolve_nodes({"cfm": [PM]}, "cfm_abc") == [PM]


# ── Engine fixtures ──────────────────────────────────────────────────────────

async def _user(db, role, bp=False):
    u = User(full_name=f"{role} holder", id=uuid.uuid4(), role=role, is_active=True)
    db.add(u)
    await db.flush()
    if bp:
        await db.execute(sa.text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_bp')"),
                         {"u": str(u.id)})
    return u


async def _world(db, chain):
    cfg = CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa_dir": copy.deepcopy(chain)})
    db.add(cfg)
    # In-flight documents are aligned to these chains (seeded by migration 0004).
    db.add(WorkflowSnapshot(id=1, workflow_defs={"pa_dir": copy.deepcopy(chain)}))
    people = {
        "requester": await _user(db, "requester"),
        "admin": await _user(db, "system_admin"),
        "procurement_manager": await _user(db, "procurement_manager"),
        "gm": await _user(db, "gm"),
        "finance_manager": await _user(db, "finance_manager"),
        "vendor_manager": await _user(db, "vendor_manager"),
        "finance_bp": await _user(db, "accountant", bp=True),
    }
    await db.flush()
    return cfg, people


async def _direct_pa(db, people, approve_through: list[str]):
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-RLN-{uuid.uuid4().hex[:6]}x", title="Direct — realign",
        status="draft", approval_step_idx=0, payment_amount=Decimal("673.00"),
        vendor_name="Pita Co", currency="CAD", invoice_ids=[str(uuid.uuid4())],
        po_id=None, source="direct", created_by=people["requester"].id)
    db.add(pa)
    await db.flush()
    # One commit per action, as in production: events of separate actions get
    # distinct created_at (now() is the transaction time), which the run
    # boundaries are read from.
    await execute_action(db, "pa", pa.id, "submit", people["requester"].id, "requester")
    await db.commit()
    for role in approve_through:
        u = people[role]
        await execute_action(db, "pa", pa.id, "approve", u.id,
                             "accountant" if role == "finance_bp" else role)
        await db.commit()
    return pa


async def _open_tasks(db, pa):
    return (await db.execute(select(Task).where(
        Task.document_id == pa.id, Task.type == "approve_pa",
        Task.is_completed.is_(False)))).scalars().all()


async def _open_tasks_by_id(db, pa_id):
    return (await db.execute(select(Task).where(
        Task.document_id == pa_id, Task.type == "approve_pa",
        Task.is_completed.is_(False)))).scalars().all()


async def _edit_chain(db, cfg, people, new_chain):
    cfg.workflow_defs = {"pa_dir": copy.deepcopy(new_chain)}
    await db.flush()
    return await realign_after_workflow_change(db, people["admin"].id)


async def _approvals_by_step(db, pa):
    rows = (await db.execute(select(ApprovalEvent.step_idx, ApprovalEvent.actor_role)
                             .where(ApprovalEvent.document_id == pa.id,
                                    ApprovalEvent.action == "approve"))).all()
    return {i: r for i, r in rows}


# ── The incident ─────────────────────────────────────────────────────────────

async def test_waiting_on_fm_moves_to_bp_and_the_task_follows(engine_db_session):
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    pa = await _direct_pa(db, people, ["procurement_manager", "gm"])
    [t] = await _open_tasks(db, pa)
    assert t.assigned_role == "finance_manager"

    out = await _edit_chain(db, cfg, people, SWAPPED)

    assert out["changed_workflows"] == ["pa_dir"] and out["errors"] == []
    [r] = out["realigned"]
    assert (r["from"], r["to"], r["completed"]) == ("finance_manager", "finance_bp", False)
    await db.refresh(pa)
    assert pa.approval_step_idx == 2
    [t] = await _open_tasks(db, pa)
    assert t.assigned_role == "finance_bp"
    # The Finance Manager can no longer take it; the BP can.
    with pytest.raises(ValueError, match="requires role: finance_bp"):
        await execute_action(db, "pa", pa.id, "approve", people["finance_manager"].id,
                             "finance_manager")
    # Then the new chain as configured: BP → VM → FM → approved.
    for role in ("finance_bp", "vendor_manager", "finance_manager"):
        await execute_action(db, "pa", pa.id, "approve", people[role].id,
                             "accountant" if role == "finance_bp" else role)
    await db.refresh(pa)
    assert pa.status == "approved"


async def test_steps_already_given_are_not_asked_again(engine_db_session):
    """PA-20261111-0008's shape: FM and VM already approved, waiting on BP (old
    last step). BP moved to step 2 — it is still owed; after it, VM and FM stand
    and the PA is approved without asking them again (rule 2)."""
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    pa = await _direct_pa(db, people, ["procurement_manager", "gm", "finance_manager",
                                       "vendor_manager"])
    [t] = await _open_tasks(db, pa)
    assert t.assigned_role == "finance_bp"

    out = await _edit_chain(db, cfg, people, SWAPPED)

    [r] = out["realigned"]
    assert r["to"] == "finance_bp"
    await db.refresh(pa)
    assert pa.approval_step_idx == 2
    [t2] = await _open_tasks(db, pa)
    assert t2.id == t.id          # same step, same approver → task untouched, no re-notify
    # The approval line pairs events with steps by index: every approval now
    # sits under the step it was given at on the NEW chain.
    assert await _approvals_by_step(db, pa) == {
        0: "procurement_manager", 1: "gm", 3: "vendor_manager", 4: "finance_manager"}

    await execute_action(db, "pa", pa.id, "approve", people["finance_bp"].id, "accountant")
    await db.refresh(pa)
    assert pa.status == "approved"
    payment = (await db.execute(select(Task).where(
        Task.document_id == pa.id, Task.type == "process_pa"))).scalar_one()
    assert payment.assigned_role == "payment_officer"


async def test_added_step_behind_is_waived_added_step_ahead_is_required(engine_db_session):
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    pa = await _direct_pa(db, people, ["procurement_manager", "gm", "finance_manager"])
    # waiting on VM; OPM added between PM and GM (passed), a second GM after VM (ahead)
    opm = await _user(db, "opm")
    new = [PM, _n("opm", "opm"), GM, FM, VM, _n("gm2", "finance_director"), BP]
    await db.execute(sa.text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_director')"),
                     {"u": str(opm.id)})

    out = await _edit_chain(db, cfg, people, new)

    [r] = out["realigned"]
    assert r["to"] == "vendor_manager"
    await db.refresh(pa)
    assert pa.approval_step_idx == 4
    waived = (await db.execute(select(ApprovalEvent).where(
        ApprovalEvent.document_id == pa.id, ApprovalEvent.comment == WAIVED_REASON))).scalars().all()
    assert [w.step_idx for w in waived] == [1]
    [t] = await _open_tasks(db, pa)
    assert t.assigned_role == "vendor_manager"
    await execute_action(db, "pa", pa.id, "approve", people["vendor_manager"].id, "vendor_manager")
    [t] = await _open_tasks(db, pa)
    assert t.assigned_role == "finance_director"     # the step added ahead is required


async def test_every_step_satisfied_approves_the_document(engine_db_session):
    """Removing the only step still owed leaves nothing to ask (rule 3)."""
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    pa = await _direct_pa(db, people, ["procurement_manager", "gm", "finance_manager",
                                       "vendor_manager"])
    out = await _edit_chain(db, cfg, people, [PM, GM, FM, VM])

    [r] = out["realigned"]
    assert r["completed"] is True and r["to"] == "Approved"
    await db.refresh(pa)
    assert pa.status == "approved"
    assert await _open_tasks(db, pa) == []
    assert (await db.execute(select(Task).where(
        Task.document_id == pa.id, Task.type == "process_pa"))).scalar_one()


async def test_label_only_edit_moves_nothing(engine_db_session):
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    pa = await _direct_pa(db, people, ["procurement_manager"])
    [t] = await _open_tasks(db, pa)
    relabelled = copy.deepcopy(OLD)
    relabelled[1]["label"] = "General Manager"
    out = await _edit_chain(db, cfg, people, relabelled)
    assert out["realigned"] == []
    [t2] = await _open_tasks(db, pa)
    assert t2.id == t.id


async def test_untouched_chain_documents_are_left_alone(engine_db_session):
    """Only documents walking a changed chain move: a PO-linked PA (`pa` chain)
    is not touched by a pa_dir edit."""
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    both = {"pa_dir": copy.deepcopy(OLD), "pa": [copy.deepcopy(FM), copy.deepcopy(BP)]}
    cfg.workflow_defs = copy.deepcopy(both)
    (await db.get(WorkflowSnapshot, 1)).workflow_defs = copy.deepcopy(both)
    await db.flush()
    po_pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-RLN-{uuid.uuid4().hex[:6]}y", title="PO PA",
        status="draft", approval_step_idx=0, payment_amount=Decimal("10.00"),
        vendor_name="V", currency="CAD", invoice_ids=[], po_id=None, source="po",
        created_by=people["requester"].id)
    db.add(po_pa)
    await db.flush()
    await execute_action(db, "pa", po_pa.id, "submit", people["requester"].id, "requester")

    cfg.workflow_defs = {**both, "pa_dir": copy.deepcopy(SWAPPED)}
    await db.flush()
    out = await realign_after_workflow_change(db, people["admin"].id)
    assert out["changed_workflows"] == ["pa_dir"]
    assert out["realigned"] == []


async def test_returned_document_resumes_at_the_first_step_still_owed(engine_db_session):
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    pa = await _direct_pa(db, people, ["procurement_manager", "gm"])
    await execute_action(db, "pa", pa.id, "return", people["finance_manager"].id,
                         "finance_manager", comment="fix the invoice")
    await db.commit()
    await db.refresh(pa)
    assert pa.status == "returned"

    out = await _edit_chain(db, cfg, people, SWAPPED)
    [r] = out["realigned"]
    assert r["to"] == "finance_bp"

    await execute_action(db, "pa", pa.id, "submit", people["requester"].id, "requester")
    await db.refresh(pa)
    assert pa.approval_step_idx == 2
    [t] = await _open_tasks(db, pa)
    assert t.assigned_role == "finance_bp"


async def test_old_approvals_on_a_restarted_list_are_not_skipped(engine_db_session):
    """The skip-already-given rule only honours an approval recorded for the
    step's own role: an approval whose index now names a different role (its
    chain changed without a realign) must not wave a step through."""
    db = engine_db_session
    cfg, people = await _world(db, [PM, GM, FM])
    pa = await _direct_pa(db, people, ["procurement_manager", "gm"])     # waiting on FM
    # Chain replaced behind the engine's back (no realign): index 1 is now VM.
    cfg.workflow_defs = {"pa_dir": [FM, VM, BP]}
    pa.approval_step_idx = 0
    await db.flush()
    await execute_action(db, "pa", pa.id, "approve", people["finance_manager"].id, "finance_manager")
    [t] = await _open_tasks(db, pa)
    assert t.assigned_role == "vendor_manager"      # GM's old approval at index 1 does not count


# ── Endpoint ─────────────────────────────────────────────────────────────────

def _client(user: User):
    token = jose_jwt.encode({"sub": str(user.id), "role": user.role},
                            settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def _use_test_db(engine_db_session):
    async def _get_db_override():
        yield engine_db_session
    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(get_db, None)


async def test_preview_reports_and_changes_nothing(engine_db_session, _use_test_db):
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    pa = await _direct_pa(db, people, ["procurement_manager", "gm"])
    pa_id, number = pa.id, pa.pa_number
    await db.commit()

    async with _client(people["admin"]) as c:
        r = await c.post("/approval/v1/routing/realign-workflows", json={
            "new_defs": {"pa_dir": SWAPPED}, "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert [(x["number"], x["from"], x["to"]) for x in body["realigned"]] == \
        [(number, "finance_manager", "finance_bp")]

    db.expire_all()
    assert (await db.execute(select(CompanyConfig))).scalar_one().workflow_defs == {"pa_dir": OLD}
    assert (await db.get(WorkflowSnapshot, 1)).workflow_defs == {"pa_dir": OLD}
    assert (await db.get(PaymentApplication, pa_id)).approval_step_idx == 2
    [t] = await _open_tasks_by_id(db, pa_id)
    assert t.assigned_role == "finance_manager"


async def test_apply_commits_and_is_idempotent(engine_db_session, _use_test_db):
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    pa = await _direct_pa(db, people, ["procurement_manager", "gm"])
    pa_id = pa.id
    admin, fm = _client(people["admin"]), _client(people["finance_manager"])
    cfg.workflow_defs = {"pa_dir": copy.deepcopy(SWAPPED)}
    await db.commit()

    # The save is gated by epms-api (admin_panel, which a Finance Manager may
    # hold); the realignment itself takes no input and only follows saved config.
    async with fm as c:
        r = await c.post("/approval/v1/routing/realign-workflows", json={})
    assert r.status_code == 200, r.text
    assert len(r.json()["realigned"]) == 1
    db.expire_all()
    [t] = await _open_tasks_by_id(db, pa_id)
    assert t.assigned_role == "finance_bp"
    assert (await db.get(WorkflowSnapshot, 1)).workflow_defs == {"pa_dir": SWAPPED}

    # Idempotent: a second call (a retry after an outage) moves nothing.
    async with admin as c:
        r = await c.post("/approval/v1/routing/realign-workflows", json={})
    assert r.json()["changed_workflows"] == [] and r.json()["realigned"] == []
    db.expire_all()
    [t2] = await _open_tasks_by_id(db, pa_id)
    assert t2.id == t.id


async def test_missed_realignment_is_caught_up_from_the_snapshot(engine_db_session):
    """approval-api was down during the save: the config changed but nothing
    moved. The snapshot still holds the old chain, so the next run does it."""
    db = engine_db_session
    cfg, people = await _world(db, OLD)
    pa = await _direct_pa(db, people, ["procurement_manager", "gm"])
    cfg.workflow_defs = {"pa_dir": copy.deepcopy(SWAPPED)}      # saved, not realigned
    await db.commit()
    # ...a later save of something unrelated triggers the realignment.
    out = await realign_after_workflow_change(db, people["admin"].id)
    assert [r["to"] for r in out["realigned"]] == ["finance_bp"]


async def test_po_signoff_moves_too_and_leaves_the_pos_own_workflow_alone(engine_db_session):
    """PO sign-off walks its own columns (signoff_step_idx) and its step task is
    `sign_po`, not approve_* — it must be found and carried over all the same,
    without touching the PO's own approval_step_idx."""
    from app.models.po import PurchaseOrder
    db = engine_db_session
    pm_node = {"id": "proc_mgr", "role": "procurement_manager", "label": "Purchasing Manager",
               "sig_slot": "initials"}
    opm_node = {"id": "opm", "role": "opm", "label": "Operations Manager", "sig_slot": "signature"}
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"posign": [pm_node, opm_node]}))
    db.add(WorkflowSnapshot(id=1, workflow_defs={"posign": [pm_node, opm_node]}))
    officer = await _user(db, "requester")
    admin = await _user(db, "system_admin")
    await _user(db, "procurement_manager")
    await _user(db, "opm")
    po = PurchaseOrder(
        id=uuid.uuid4(), number=f"PO-RLN-{uuid.uuid4().hex[:6]}x", title="NC import",
        status="nc_pending", approval_step_idx=3, total=Decimal("1000.00"),
        vendor_name="Acme", is_prepaid=False, pr_id=None, created_by=admin.id,
        signoff_status="draft", signoff_step_idx=0, signoff_submitted_by=officer.id)
    db.add(po)
    await db.flush()
    await execute_action(db, "posign", po.id, "submit", officer.id, "requester")
    await db.commit()

    cfg = (await db.execute(select(CompanyConfig))).scalar_one()
    cfg.workflow_defs = {"posign": [opm_node, pm_node]}       # order swapped
    await db.flush()
    out = await realign_after_workflow_change(db, admin.id)

    [r] = out["realigned"]
    assert (r["doc_type"], r["to"]) == ("posign", "Operations Manager")
    await db.refresh(po)
    assert (po.signoff_step_idx, po.approval_step_idx) == (0, 3)
    [t] = (await db.execute(select(Task).where(
        Task.document_id == po.id, Task.type == "sign_po",
        Task.is_completed.is_(False)))).scalars().all()
    assert t.assigned_role == "opm"
