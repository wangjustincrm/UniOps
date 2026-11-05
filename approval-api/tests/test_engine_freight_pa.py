"""Freight PA: a `pa` document that walks the `pa_frt` chain.

A courier invoice spans several departments, so the Freight PA chain
(OPM → GM → Finance Manager → Finance BP) must not contain any step that
resolves by department — and an ordinary PO PA must keep walking `pa`'s chain.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import (_WORKFLOW_DEFAULTS, build_effective_workflow, execute_action,
                             workflow_key)
from app.models.config import CompanyConfig
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.user import User


async def _holder(db, role: str) -> User:
    u = User(full_name=f"{role} holder", id=uuid.uuid4(), role=role, is_active=True)
    db.add(u)
    await db.flush()
    if role == "finance_bp":
        # finance_bp is a function, not a singleton post: read from user_roles only.
        await db.execute(sa.text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r)"),
                         {"u": str(u.id), "r": role})
    return u


async def _pa(db, created_by, source: str) -> PaymentApplication:
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-FRT-{uuid.uuid4().hex[:6]}", title="Freight — UPS",
        status="submitted", approval_step_idx=0, payment_amount=Decimal("86.82"),
        vendor_name="UPS Canada", currency="CAD", invoice_ids=[str(uuid.uuid4())],
        po_id=None, source=source, created_by=created_by)
    db.add(pa)
    await db.flush()
    return pa


def test_default_chain_has_no_department_resolved_step():
    roles = [s["role"] for s in _WORKFLOW_DEFAULTS["pa_frt"]]
    assert roles == ["opm", "gm", "finance_manager", "finance_bp"]
    assert not {"gm_or_opm", "dept_manager", "director", "supervisor"} & set(roles)


def test_workflow_key_only_diverts_freight_pas():
    class Doc:
        def __init__(self, source):
            self.source = source
    assert workflow_key("pa", Doc("freight")) == "pa_frt"
    # Positive controls: everything else keeps its own key.
    assert workflow_key("pa", Doc("po")) == "pa"
    assert workflow_key("pa", Doc("agreement")) == "pa"
    assert workflow_key("pa_dir", Doc("freight")) == "pa_dir"
    assert workflow_key("pr", object()) == "pr"


@pytest.mark.asyncio
async def test_effective_workflow_per_source(engine_db_session):
    db = engine_db_session
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={}))
    req = await _holder(db, "requester")
    freight = await _pa(db, req.id, "freight")
    po_pa = await _pa(db, req.id, "po")
    assert [s["role"] for s in await build_effective_workflow(db, "pa", freight, None)] == \
        ["opm", "gm", "finance_manager", "finance_bp"]
    assert [s["role"] for s in await build_effective_workflow(db, "pa", po_pa, None)] == \
        [s["role"] for s in _WORKFLOW_DEFAULTS["pa"]]


@pytest.mark.asyncio
async def test_admin_edited_chain_is_used(engine_db_session):
    db = engine_db_session
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={
        "pa_frt": [{"id": "fm", "role": "finance_manager", "label": "Finance Manager"}]}))
    req = await _holder(db, "requester")
    freight = await _pa(db, req.id, "freight")
    assert [s["role"] for s in await build_effective_workflow(db, "pa", freight, None)] == \
        ["finance_manager"]


@pytest.mark.asyncio
async def test_freight_pa_walks_four_steps_then_payment_task(engine_db_session):
    db = engine_db_session
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={}))
    req = await _holder(db, "requester")
    opm, gm = await _holder(db, "opm"), await _holder(db, "gm")
    fm, fbp = await _holder(db, "finance_manager"), await _holder(db, "finance_bp")
    pa = await _pa(db, req.id, "freight")

    # Out of order is refused: the GM cannot take the OPM's step.
    with pytest.raises(ValueError, match="requires role: opm"):
        await execute_action(db, "pa", pa.id, "approve", gm.id, "gm")

    for actor, role, expected_status in ((opm, "opm", "in_review"), (gm, "gm", "in_review"),
                                         (fm, "finance_manager", "in_review"),
                                         (fbp, "finance_bp", "approved")):
        result = await execute_action(db, "pa", pa.id, "approve", actor.id, role)
        assert result.new_status == expected_status, role

    task = (await db.execute(sa.select(Task).where(
        Task.document_id == pa.id, Task.type == "process_pa"))).scalar_one()
    # Still a `pa` document: the inbox, liveness and payment paths need no new type.
    assert task.document_type == "pa"
    assert task.assigned_role == "payment_officer"
