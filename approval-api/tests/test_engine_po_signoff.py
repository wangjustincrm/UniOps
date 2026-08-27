"""posign — the PO sign-off workflow that runs alongside a PO's own approval.

An NC-imported PO is created by the nc-sync service account and its `status`
is rewritten from NC's forderstatus on every sync. The sign-off therefore
cannot live on `status` / `approval_step_idx`; it walks signoff_status /
signoff_step_idx through _DOC_META's status_attr / step_attr indirection.

What these tests pin down is the separation: running a sign-off to completion
must leave the PO's own workflow columns byte-for-byte untouched, and the
return task must reach the person who raised the sign-off rather than the
service account that owns created_by.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import _DOC_META, _WORKFLOW_DEFAULTS, _resolve_meta, execute_action
from app.models.config import CompanyConfig
from app.models.po import PurchaseOrder
from app.models.task import Task
from app.models.user import User

pytestmark = pytest.mark.asyncio

_WORKFLOW = [
    {"id": "proc_mgr", "role": "procurement_manager",
     "label": "Purchasing Manager", "sig_slot": "initials"},
    {"id": "opm", "role": "opm", "label": "Operations Manager",
     "sig_slot": "signature"},
]


async def _seed(db):
    """nc-sync owns created_by; the officer raises the sign-off; PM and OPM sign."""
    nc_sync = User(id=uuid.uuid4(), full_name="NC Sync", role="system_admin",
                   is_active=True)
    officer = User(id=uuid.uuid4(), full_name="PA Officer", role="requester",
                   is_active=True)
    pm = User(id=uuid.uuid4(), full_name="Purchasing Manager",
              role="procurement_manager", is_active=True)
    opm = User(id=uuid.uuid4(), full_name="Ops Manager", role="opm", is_active=True)
    db.add_all([nc_sync, officer, pm, opm])
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"posign": _WORKFLOW}))
    await db.flush()
    return nc_sync, officer, pm, opm


async def _make_po(db, nc_sync, officer):
    po = PurchaseOrder(
        id=uuid.uuid4(), number=f"PO-{uuid.uuid4().hex[:8]}", title="NC import",
        # The PO's OWN workflow columns, deliberately at non-default values so
        # any write by the sign-off engine shows up.
        status="nc_pending", approval_step_idx=3,
        total=Decimal("1000.00"), vendor_name="Acme", is_prepaid=False,
        pr_id=None, created_by=nc_sync.id,
        signoff_status="draft", signoff_step_idx=0,
        signoff_submitted_by=officer.id,
    )
    db.add(po)
    await db.flush()
    return po


# ── wiring ────────────────────────────────────────────────────────────────

def test_posign_reads_and_writes_the_signoff_columns():
    meta = _resolve_meta("posign")
    assert meta["model"].__tablename__ == "purchase_orders"
    assert meta["status_attr"] == "signoff_status"
    assert meta["step_attr"] == "signoff_step_idx"
    # Its tasks must not collide with the PO's own approve_po / revise_po.
    assert meta["task_approve"] == "sign_po"
    assert meta["task_revise"] == "revise_po_signoff"


def test_posign_default_workflow_matches_the_paper_form():
    steps = _WORKFLOW_DEFAULTS["posign"]
    assert [s["role"] for s in steps] == ["procurement_manager", "opm"]
    # Purchasing Manager initials between the blocks, OPM signs ours.
    assert [s.get("sig_slot") for s in steps] == ["initials", "signature"]


def test_po_and_posign_read_different_cursors():
    po_meta, sig_meta = _DOC_META["po"], _DOC_META["posign"]
    assert po_meta.get("step_attr", "approval_step_idx") == "approval_step_idx"
    assert sig_meta["step_attr"] != po_meta.get("step_attr", "approval_step_idx")


# ── behaviour ─────────────────────────────────────────────────────────────

async def test_signoff_runs_to_completion_without_touching_the_pos_own_columns(
    engine_db_session,
):
    db = engine_db_session
    nc_sync, officer, pm, opm = await _seed(db)
    po = await _make_po(db, nc_sync, officer)

    await execute_action(db, "posign", po.id, "submit", officer.id, "requester",
                         comment="Raw material for the September run.")
    assert po.signoff_status == "submitted"
    assert po.signoff_step_idx == 0

    await execute_action(db, "posign", po.id, "approve", pm.id, "procurement_manager")
    assert po.signoff_status == "in_review"
    assert po.signoff_step_idx == 1

    await execute_action(db, "posign", po.id, "approve", opm.id, "opm")
    assert po.signoff_status == "approved"

    # The whole point: the PO's own workflow is exactly where it was.
    assert po.status == "nc_pending"
    assert po.approval_step_idx == 3


async def test_sign_task_goes_to_the_step_role_and_is_scoped_to_posign(
    engine_db_session,
):
    db = engine_db_session
    nc_sync, officer, pm, opm = await _seed(db)
    po = await _make_po(db, nc_sync, officer)

    await execute_action(db, "posign", po.id, "submit", officer.id, "requester",
                         comment="Why we are buying this.")
    task = (await db.execute(sa.select(Task).where(
        Task.document_id == po.id, Task.is_completed.is_(False)))).scalar_one()
    assert task.type == "sign_po"
    assert task.document_type == "posign"
    assert task.assigned_role == "procurement_manager"
    # Singleton post → broadcast, so reassigning the post does not orphan it.
    assert task.assigned_user_id is None
    assert task.title.startswith("Sign PO:")
    assert "signature required" in (task.description or "")


async def test_return_reaches_the_submitter_not_the_nc_service_account(
    engine_db_session,
):
    db = engine_db_session
    nc_sync, officer, pm, opm = await _seed(db)
    po = await _make_po(db, nc_sync, officer)

    await execute_action(db, "posign", po.id, "submit", officer.id, "requester",
                         comment="Initial justification.")
    await execute_action(db, "posign", po.id, "return", pm.id, "procurement_manager",
                         comment="Why this quantity?")

    assert po.signoff_status == "returned"
    assert po.signoff_step_idx == 0
    revise = (await db.execute(sa.select(Task).where(
        Task.type == "revise_po_signoff", Task.is_completed.is_(False)))).scalar_one()
    # created_by is the nc-sync service account — nobody logs into it.
    assert revise.assigned_user_id == officer.id
    assert revise.assigned_user_id != po.created_by


async def test_a_returned_signoff_can_be_resubmitted(engine_db_session):
    db = engine_db_session
    nc_sync, officer, pm, opm = await _seed(db)
    po = await _make_po(db, nc_sync, officer)

    await execute_action(db, "posign", po.id, "submit", officer.id, "requester",
                         comment="First attempt.")
    await execute_action(db, "posign", po.id, "return", pm.id, "procurement_manager",
                         comment="Not enough detail.")
    await execute_action(db, "posign", po.id, "submit", officer.id, "requester",
                         comment="Added the detail you asked for.")

    assert po.signoff_status == "submitted"
    assert po.signoff_step_idx == 0
    open_sign = (await db.execute(sa.select(Task).where(
        Task.type == "sign_po", Task.is_completed.is_(False)))).scalars().all()
    assert len(open_sign) == 1
