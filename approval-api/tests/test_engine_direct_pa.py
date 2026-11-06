"""EPMS Direct PA: a `pa` document (source='direct') that walks the `pa_dir`
chain — the "PA (Direct)" workflow OA's retired Direct PA used — and routes
department steps on the department named on the PA itself.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import (_WORKFLOW_DEFAULTS, _get_dept_manager_id, _routing_department_id,
                             build_effective_workflow, execute_action, workflow_key)
from app.models.config import CompanyConfig
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.user import User


async def _holder(db, role: str, department_id=None) -> User:
    u = User(full_name=f"{role} holder", id=uuid.uuid4(), role=role, is_active=True,
             department_id=department_id)
    db.add(u)
    await db.flush()
    if role == "finance_bp":
        # finance_bp is a function, not a singleton post: read from user_roles only.
        await db.execute(sa.text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r)"),
                         {"u": str(u.id), "r": role})
    return u


async def _pa(db, created_by, source="direct", department_id=None, status="submitted"):
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-DIR-{uuid.uuid4().hex[:6]}", title="Direct — calibration",
        status=status, approval_step_idx=0, payment_amount=Decimal("1130.00"),
        vendor_name="Calibration Co", currency="CAD", invoice_ids=[str(uuid.uuid4())],
        po_id=None, source=source, department_id=department_id, created_by=created_by)
    db.add(pa)
    await db.flush()
    return pa


def test_workflow_key_diverts_direct_pas_to_pa_dir():
    class Doc:
        def __init__(self, source):
            self.source = source
    assert workflow_key("pa", Doc("direct")) == "pa_dir"
    # Positive controls: everything else keeps its own key.
    assert workflow_key("pa", Doc("freight")) == "pa_frt"
    assert workflow_key("pa", Doc("po")) == "pa"
    assert workflow_key("pa", Doc("oa_direct")) == "pa"
    assert workflow_key("pa_dir", Doc("oa_direct")) == "pa_dir"


@pytest.mark.asyncio
async def test_effective_workflow_is_the_direct_chain(engine_db_session):
    db = engine_db_session
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={}))
    req = await _holder(db, "requester")
    direct = await _pa(db, req.id)
    assert [s["role"] for s in await build_effective_workflow(db, "pa", direct, None)] == \
        [s["role"] for s in _WORKFLOW_DEFAULTS["pa_dir"]]


@pytest.mark.asyncio
async def test_direct_pa_walks_the_default_chain_then_payment_task(engine_db_session):
    db = engine_db_session
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={}))
    req = await _holder(db, "requester")
    fbp, fm = await _holder(db, "finance_bp"), await _holder(db, "finance_manager")
    pa = await _pa(db, req.id)

    # Out of order is refused: the Finance Manager cannot take Finance BP's step.
    with pytest.raises(ValueError, match="requires role: finance_bp"):
        await execute_action(db, "pa", pa.id, "approve", fm.id, "finance_manager")

    r = await execute_action(db, "pa", pa.id, "approve", fbp.id, "finance_bp")
    assert r.new_status == "in_review"
    r = await execute_action(db, "pa", pa.id, "approve", fm.id, "finance_manager")
    assert r.new_status == "approved"

    task = (await db.execute(sa.select(Task).where(
        Task.document_id == pa.id, Task.type == "process_pa"))).scalar_one()
    # Still a `pa` document: inbox, liveness and payment need no new type.
    assert task.document_type == "pa"
    assert task.assigned_role == "payment_officer"


@pytest.mark.asyncio
async def test_department_step_routes_on_the_pas_department(engine_db_session):
    """An admin adds Department Manager to the PA (Direct) chain: the approver is
    the manager of the department named on the PA, not of its creator's. Both
    departments have a manager, so a lookup that resolved nobody cannot pass."""
    db = engine_db_session
    pa_dept, creator_dept = uuid.uuid4(), uuid.uuid4()
    pa_mgr = await _holder(db, "dept_manager", pa_dept)
    creator_mgr = await _holder(db, "dept_manager", creator_dept)
    creator = await _holder(db, "ap_clerk", creator_dept)
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa_dir": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "finance_mgr", "role": "finance_manager", "label": "Finance Manager"}]}))
    pa = await _pa(db, creator.id, department_id=pa_dept, status="draft")

    assert await _routing_department_id(db, "pa", pa, creator.id) == pa_dept
    assert await _get_dept_manager_id(db, pa_dept) == pa_mgr.id

    await execute_action(db, "pa", pa.id, "submit", creator.id, "ap_clerk")
    task = (await db.execute(sa.select(Task).where(
        Task.document_id == pa.id, Task.type == "approve_pa",
        Task.is_completed.is_(False)))).scalar_one()
    assert task.assigned_user_id == pa_mgr.id
    assert task.assigned_user_id != creator_mgr.id
