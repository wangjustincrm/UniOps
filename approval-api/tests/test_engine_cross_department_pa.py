"""A payment application covering several departments.

One vendor invoice routinely covers purchase orders from several departments —
the vendor has no idea where those boundaries are — so the payment cannot be
split along them without splitting the document finance reviews as a whole.

That leaves the department-scoped approval steps with nothing to resolve: the
engine picks approvers from the PRIMARY purchase order's department, which
would put one department's manager in charge of another department's spend
without the other ever seeing it. So for these payments the Department Manager
and Director steps step aside — visibly, with a reason on the timeline — and
the GM/OPM step resolves from an engine-wide setting instead of from a
department that cannot be chosen (one may say GM, another OPM).
"""
import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import select

from app.crud.engine import execute_action
from app.crud.workflow import pa_department_ids
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.routing import ApprovalSetting, CROSS_DEPT_GM_OR_OPM, DeptRouting
from app.models.task import Task
from app.models.user import User

# The workflow production actually runs (company_config.workflow_defs['pa']),
# not the two-step default in code — the department-scoped steps only exist here.
_PA_WORKFLOW = [
    {"id": "s0", "role": "dept_manager",    "label": "Department Manager"},
    {"id": "s1", "role": "director",        "label": "Director"},
    {"id": "s2", "role": "gm_or_opm",       "label": "GM/OPM"},
    {"id": "s3", "role": "finance_manager", "label": "Finance Manager"},
]


async def _user(db, role, dept_id=None, name="U"):
    u = User(id=uuid.uuid4(), full_name=name, role=role,
             department_id=dept_id, is_active=True)
    db.add(u)
    await db.flush()
    return u


async def _po_in_dept(db, creator, dept_id):
    """A PO whose PR carries `dept_id` — the resolution the engine uses."""
    pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:6]}", title="T", status="approved",
                         approval_step_idx=0, amount=Decimal("10"),
                         created_by=creator.id, department_id=dept_id)
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="T", status="issued",
                       approval_step_idx=0, total=Decimal("10"),
                       created_by=creator.id, pr_id=pr.id, vendor_name="V")
    db.add(po)
    await db.flush()
    return po


async def _pa_over(db, creator, pos):
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-{uuid.uuid4().hex[:6]}", title="T",
        status="draft", approval_step_idx=0, payment_amount=Decimal("10"),
        vendor_name="V", currency="CAD",
        po_id=pos[0].id, po_number=pos[0].number,
        invoice_ids=[], created_by=creator.id)
    db.add(pa)
    await db.flush()
    for i, po in enumerate(pos):
        await db.execute(sa.text(
            "INSERT INTO pa_po_links (id, pa_id, po_id, po_number, sort_order)"
            " VALUES (:id, :pa, :po, :n, :s)"),
            {"id": str(uuid.uuid4()), "pa": str(pa.id), "po": str(po.id),
             "n": po.number, "s": i})
    return pa


async def _workflow(db):
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa": _PA_WORKFLOW},
                         budget_admin_config={}))
    await db.flush()


# ── pa_department_ids ─────────────────────────────────────────────────────────

async def test_department_ids_span_every_po(engine_db_session):
    db = engine_db_session
    creator = await _user(db, "requester")
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    po1 = await _po_in_dept(db, creator, d1)
    po2 = await _po_in_dept(db, creator, d2)
    pa = await _pa_over(db, creator, [po1, po2])

    assert await pa_department_ids(db, pa.id) == {str(d1), str(d2)}


async def test_a_po_with_no_pr_is_its_own_department(engine_db_session):
    """NULL is a real element, not a gap: a PO with no PR routes to the
    submitter's own department, so mixing one in genuinely spans two routings."""
    db = engine_db_session
    creator = await _user(db, "requester")
    d1 = uuid.uuid4()
    po1 = await _po_in_dept(db, creator, d1)
    po2 = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="NC", status="issued",
                        approval_step_idx=0, total=Decimal("10"),
                        created_by=creator.id, pr_id=None, vendor_name="V")
    db.add(po2)
    await db.flush()
    pa = await _pa_over(db, creator, [po1, po2])

    assert await pa_department_ids(db, pa.id) == {str(d1), None}


async def test_header_po_counts_even_without_a_link_row(engine_db_session):
    """A PA written outside epms-api's PA crud has only the header. Reading the
    link table alone would report it as spanning NO department and silently send
    it down the cross-department path."""
    db = engine_db_session
    creator = await _user(db, "requester")
    d1 = uuid.uuid4()
    po1 = await _po_in_dept(db, creator, d1)
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-{uuid.uuid4().hex[:6]}", title="T",
        status="draft", approval_step_idx=0, payment_amount=Decimal("10"),
        vendor_name="V", currency="CAD", po_id=po1.id, po_number=po1.number,
        invoice_ids=[], created_by=creator.id)
    db.add(pa)
    await db.flush()

    assert await pa_department_ids(db, pa.id) == {str(d1)}


# ── Submitting a cross-department PA ──────────────────────────────────────────

async def _submit_cross_dept_pa(db, *, cross_setting=None):
    await _workflow(db)
    creator = await _user(db, "ap_clerk", name="Clerk")
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    # Both departments have a real manager and director configured, so any skip
    # below is the cross-department rule and not "nobody is configured".
    mgr1 = await _user(db, "dept_manager", d1, "Mgr One")
    mgr2 = await _user(db, "dept_manager", d2, "Mgr Two")
    dir1 = await _user(db, "director", d1, "Dir One")
    gm = await _user(db, "gm", None, "The GM")
    opm = await _user(db, "opm", None, "The OPM")
    db.add_all([
        DeptRouting(dept_id=d1, gm_or_opm="gm", director_user_id=dir1.id),
        DeptRouting(dept_id=d2, gm_or_opm="opm", director_user_id=dir1.id),
    ])
    if cross_setting is not None:
        db.add(ApprovalSetting(key=CROSS_DEPT_GM_OR_OPM, value=cross_setting))
    await db.flush()

    po1 = await _po_in_dept(db, creator, d1)
    po2 = await _po_in_dept(db, creator, d2)
    pa = await _pa_over(db, creator, [po1, po2])
    await execute_action(db, "pa", pa.id, "submit", creator.id, "ap_clerk")
    await db.flush()
    return pa, {"gm": gm, "opm": opm, "mgr1": mgr1, "mgr2": mgr2, "dir1": dir1}


async def test_cross_department_pa_skips_manager_and_director(engine_db_session):
    db = engine_db_session
    pa, _ = await _submit_cross_dept_pa(db)

    events = (await db.execute(
        select(ApprovalEvent).where(ApprovalEvent.document_id == pa.id)
        .order_by(ApprovalEvent.step_idx))).scalars().all()
    skipped = {e.step_idx: e.comment for e in events if e.action == "approve"}
    assert set(skipped) == {0, 1}, f"expected steps 0 and 1 skipped, got {skipped}"
    for comment in skipped.values():
        # The timeline keys on this prefix to render the step struck through,
        # and the rest of the sentence is what tells the reader WHY.
        assert comment.startswith("Auto-skipped")
        assert "several" in comment and "departments" in comment

    # ...and lands on GM/OPM, not on either department's manager.
    assert pa.approval_step_idx == 2


async def test_cross_department_gm_opm_comes_from_the_setting_not_the_department(engine_db_session):
    """d1 routes to GM and d2 to OPM — there is no principled way to pick, so
    the engine-wide setting decides. Here it says OPM."""
    db = engine_db_session
    pa, who = await _submit_cross_dept_pa(db, cross_setting="opm")

    task = (await db.execute(select(Task).where(
        Task.document_id == pa.id, Task.is_completed.is_(False)))).scalars().one()
    assert task.assigned_role == "opm"
    # Broadcast by design: gm/opm are singleton POSTS, and pinning the task to
    # whoever holds one today orphans it the moment the post is reassigned
    # (a real production incident). Who may act on it is settled by
    # post_holder_ids at approve time — see the authorization test below.
    assert task.assigned_user_id is None


async def test_cross_department_defaults_to_gm_when_unset(engine_db_session):
    """No row in approval_settings must not mean 'no approver'."""
    db = engine_db_session
    pa, who = await _submit_cross_dept_pa(db)

    task = (await db.execute(select(Task).where(
        Task.document_id == pa.id, Task.is_completed.is_(False)))).scalars().one()
    assert task.assigned_role == "gm"
    assert task.assigned_user_id is None      # broadcast — see the test above


async def test_single_department_pa_still_routes_by_department(engine_db_session):
    """The whole point is that ordinary payments are untouched: one department,
    so its manager approves and nothing is skipped."""
    db = engine_db_session
    await _workflow(db)
    creator = await _user(db, "ap_clerk", name="Clerk")
    d1 = uuid.uuid4()
    mgr1 = await _user(db, "dept_manager", d1, "Mgr One")
    dir1 = await _user(db, "director", d1, "Dir One")
    await _user(db, "gm", None, "The GM")
    db.add(DeptRouting(dept_id=d1, gm_or_opm="gm", director_user_id=dir1.id))
    await db.flush()

    po1 = await _po_in_dept(db, creator, d1)
    po2 = await _po_in_dept(db, creator, d1)          # two POs, ONE department
    pa = await _pa_over(db, creator, [po1, po2])
    await execute_action(db, "pa", pa.id, "submit", creator.id, "ap_clerk")
    await db.flush()

    assert pa.approval_step_idx == 0, "nothing should have been skipped"
    task = (await db.execute(select(Task).where(
        Task.document_id == pa.id, Task.is_completed.is_(False)))).scalars().one()
    assert task.assigned_role == "dept_manager"
    assert task.assigned_user_id == mgr1.id


async def test_the_configured_post_holder_can_actually_approve(engine_db_session):
    """Routing the task to OPM is only half of it — the authorization check
    reads the same setting, or the approver sees the task and cannot act on it
    (the 'Approve 无效' failure mode from the multi-holder bug)."""
    db = engine_db_session
    pa, who = await _submit_cross_dept_pa(db, cross_setting="opm")

    await execute_action(db, "pa", pa.id, "approve", who["opm"].id, "opm")
    await db.flush()
    assert pa.approval_step_idx == 3, "OPM's approval should advance to Finance Manager"
