"""Engine return / cancel authorization.

Both branches used to check only the document's STATUS, never who was asking.
`reject` — the branch sitting between them — has had an `_actor_can_approve`
gate since the broadcast-task incident; return and cancel were left open, and
the calling services do not cover for them (epms-api's pr/po/pa action
endpoints and expense-api's expense_action/pa_action forward to the engine with
no authorization of their own). So any logged-in user who knew a document id
could bounce anyone's in-flight document back to its submitter, or kill a draft
outright.

return  → same authority as approve/reject (the current step's approver).
cancel  → owner, an approver currently holding it, or system_admin.
"""
import uuid
from decimal import Decimal

import pytest

from app.crud.engine import execute_action
from app.models.config import CompanyConfig
from app.models.pa import PaymentApplication
from app.models.user import User

# Single-step chain so `current_step_role` is unambiguous at step 0. finance_manager
# is a plain named post — _actor_is_step_holder resolves it through post_holder_ids,
# with none of dept_manager / director / gm_or_opm's routing machinery in the way.
_PA_DIR_WORKFLOW = [
    {"id": "s0", "role": "finance_manager", "label": "Finance Manager"},
]


async def _user(db, role: str, name: str = "U") -> User:
    u = User(id=uuid.uuid4(), full_name=name, role=role, is_active=True)
    db.add(u)
    await db.flush()
    return u


async def _setup(db, status: str):
    """A PA-DIR in `status` at step 0, plus owner / approver / bystander."""
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa_dir": _PA_DIR_WORKFLOW},
                         budget_admin_config={}))
    owner = await _user(db, "requester", "Owner")
    approver = await _user(db, "finance_manager", "Approver")
    bystander = await _user(db, "requester", "Bystander")
    await db.flush()
    pa = PaymentApplication(
        id=uuid.uuid4(),
        pa_number=f"PA-{uuid.uuid4().hex[:6]}",
        title="Direct Payment — Titan Power Ltd",
        status=status,
        approval_step_idx=0,
        payment_amount=Decimal("1130.00"),
        vendor_name="Titan Power Ltd",
        currency="CAD",
        invoice_ids=[],
        created_by=owner.id,
    )
    db.add(pa)
    await db.flush()
    return pa, owner, approver, bystander


# ── return ────────────────────────────────────────────────────────────────────

async def test_return_by_bystander_rejected(engine_db_session):
    db = engine_db_session
    pa, _owner, _approver, bystander = await _setup(db, "submitted")

    with pytest.raises(ValueError, match="Not authorized to return"):
        await execute_action(db, "pa_dir", pa.id, "return", bystander.id, "requester")

    await db.refresh(pa)
    assert pa.status == "submitted"


async def test_return_by_owner_rejected(engine_db_session):
    """The submitter cannot return their own document to themselves — returning
    is the approver's decision, and `recall` is the submitter's equivalent."""
    db = engine_db_session
    pa, owner, _approver, _bystander = await _setup(db, "submitted")

    with pytest.raises(ValueError, match="Not authorized to return"):
        await execute_action(db, "pa_dir", pa.id, "return", owner.id, "requester")

    await db.refresh(pa)
    assert pa.status == "submitted"


async def test_return_by_current_approver_succeeds(engine_db_session):
    db = engine_db_session
    pa, _owner, approver, _bystander = await _setup(db, "submitted")

    result = await execute_action(db, "pa_dir", pa.id, "return", approver.id, "finance_manager")

    assert result.new_status == "returned"
    assert pa.approval_step_idx == 0


async def test_return_by_system_admin_succeeds(engine_db_session):
    db = engine_db_session
    pa, _owner, _approver, _bystander = await _setup(db, "submitted")
    admin = await _user(db, "system_admin", "Admin")

    result = await execute_action(db, "pa_dir", pa.id, "return", admin.id, "system_admin")

    assert result.new_status == "returned"


# ── cancel ────────────────────────────────────────────────────────────────────

async def test_cancel_by_bystander_rejected(engine_db_session):
    db = engine_db_session
    pa, _owner, _approver, bystander = await _setup(db, "returned")

    with pytest.raises(ValueError, match="Not authorized to cancel"):
        await execute_action(db, "pa_dir", pa.id, "cancel", bystander.id, "requester")

    await db.refresh(pa)
    assert pa.status == "returned"


async def test_cancel_by_owner_succeeds(engine_db_session):
    db = engine_db_session
    pa, owner, _approver, _bystander = await _setup(db, "returned")

    result = await execute_action(db, "pa_dir", pa.id, "cancel", owner.id, "requester")

    assert result.new_status == "cancelled"


async def test_cancel_by_current_approver_succeeds(engine_db_session):
    """An approver holding the document may cancel it — the same authority
    `reject` already gives them, which lands on the identical end state."""
    db = engine_db_session
    pa, _owner, approver, _bystander = await _setup(db, "returned")

    result = await execute_action(db, "pa_dir", pa.id, "cancel", approver.id, "finance_manager")

    assert result.new_status == "cancelled"


async def test_cancel_by_system_admin_succeeds(engine_db_session):
    db = engine_db_session
    pa, _owner, _approver, _bystander = await _setup(db, "returned")
    admin = await _user(db, "system_admin", "Admin")

    result = await execute_action(db, "pa_dir", pa.id, "cancel", admin.id, "system_admin")

    assert result.new_status == "cancelled"
