"""Engine recall authorization — only the submitter (or system_admin) may recall.

Regression test for the OA PA-DIR bug where the detail page showed "Recall to
Draft" to approvers and the engine accepted a recall from any actor.
"""
import uuid
from decimal import Decimal

import pytest

from app.crud.engine import execute_action
from app.models.pa import PaymentApplication
from app.models.user import User


async def _make_submitted_pa(db) -> tuple[PaymentApplication, User, User]:
    owner = User(full_name="Test User", id=uuid.uuid4(), role="requester", is_active=True)
    other = User(full_name="Test User", id=uuid.uuid4(), role="finance_bp", is_active=True)
    db.add_all([owner, other])
    await db.flush()  # users must land before the PA row (FK created_by → users.id)
    pa = PaymentApplication(
        pa_number=f"PA-20260611-{uuid.uuid4().hex[:4]}",
        title="Direct Payment — Titan Power Ltd",
        status="submitted",
        approval_step_idx=0,
        payment_amount=Decimal("1130.00"),
        vendor_name="Titan Power Ltd",
        currency="CAD",
        invoice_ids=[],
        created_by=owner.id,
    )
    db.add(pa)
    await db.flush()
    return pa, owner, other


async def test_recall_by_non_submitter_rejected(engine_db_session):
    db = engine_db_session
    pa, _owner, other = await _make_submitted_pa(db)

    with pytest.raises(ValueError, match="submitter"):
        await execute_action(db, "pa_dir", pa.id, "recall", other.id, "finance_bp")

    await db.refresh(pa)
    assert pa.status == "submitted"


async def test_recall_by_submitter_succeeds(engine_db_session):
    db = engine_db_session
    pa, owner, _other = await _make_submitted_pa(db)

    result = await execute_action(db, "pa_dir", pa.id, "recall", owner.id, "requester")

    assert result.new_status == "draft"
    assert pa.approval_step_idx == 0


async def test_recall_by_system_admin_allowed(engine_db_session):
    db = engine_db_session
    pa, _owner, _other = await _make_submitted_pa(db)
    admin = User(full_name="Test User", id=uuid.uuid4(), role="system_admin", is_active=True)
    db.add(admin)
    await db.flush()

    result = await execute_action(db, "pa_dir", pa.id, "recall", admin.id, "system_admin")

    assert result.new_status == "draft"
