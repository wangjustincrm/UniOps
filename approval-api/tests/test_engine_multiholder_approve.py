"""Multi-holder / multi-role approval authorization.

Two related production bugs (hanchenggang: Department Manager + GM):

  1. A user who legitimately holds the gm/opm post — as a PRIMARY role or an
     ADDITIONAL identity user_roles role — but who is NOT the arbitrarily-chosen
     `_post_holders()['gm'][0]` (first by uid sort) was DENIED at the gm_or_opm
     approve step. `_actor_can_approve` collapsed the post to a single approver
     (`rm['<role>_user_id']` = holders[0]) and checked equality, so when the post
     has 2+ holders (the singleton invariant broken — `_post_holders` itself
     warns about this), every holder except the first got "Not authorized".
     The broadcast approve task is visible to ALL holders (get_for_role matches
     by assigned_role), so the symptom was: the approver sees the task, clicks
     Approve, nothing happens ("Approve 无效").

  2. reject/cancel had NO authorization check at all, so anyone who could see a
     broadcast task could reject it ("Reject 生效" even when Approve was denied).

Fix: authorize a post step by holder MEMBERSHIP (actor ∈ all holders of the
resolved role), and gate reject with the same check.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import _actor_can_approve, execute_action
from app.crud.workflow import get_dept_gm_opm_mapping, get_role_management
from app.models.config import CompanyConfig
from app.models.pa import PaymentApplication
from app.models.routing import DeptRouting
from app.models.user import User

# uids chosen so gm_first sorts before `actor` by uid string → gm_first is
# _post_holders()['gm'][0]; actor is a holder but NOT the first.
_GM_FIRST_ID = uuid.UUID(int=1)
_ACTOR_ID = uuid.UUID(int=(1 << 128) - 1)


async def _seed_two_gm_holders(db, dept_id):
    """requester in dept_id + two gm holders (gm_first primary, actor additional)."""
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    gm_first = User(id=_GM_FIRST_ID, role="gm", is_active=True)
    actor = User(id=_ACTOR_ID, role="dept_manager", is_active=True)
    db.add_all([requester, gm_first, actor])
    await db.flush()
    # actor holds gm as an ADDITIONAL role (identity user_roles).
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'gm')"), {"u": str(actor.id)})
    db.add(DeptRouting(dept_id=dept_id, gm_or_opm="gm", supervisor_enabled=False))
    await db.flush()
    return requester, gm_first, actor


@pytest.mark.asyncio
async def test_actor_can_approve_gm_or_opm_allows_any_holder_not_just_first(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, gm_first, actor = await _seed_two_gm_holders(db, dept_id)

    rm = await get_role_management(db)
    dept_gm_opm = await get_dept_gm_opm_mapping(db)
    fbp = {uuid.UUID(u) for u in rm.get("finance_bp_user_ids", [])}

    # Precondition: the single collapsed holder is gm_first, NOT our actor.
    assert rm["gm_user_id"] == str(gm_first.id)
    assert rm["gm_user_id"] != str(actor.id)

    ok = await _actor_can_approve(
        db, "gm_or_opm", actor.id, "dept_manager", requester.id, rm, dept_gm_opm, fbp)
    assert ok, (
        "a GM role-holder who is not _post_holders['gm'][0] must still be "
        "authorized to approve the gm_or_opm step (multi-holder membership)"
    )


@pytest.mark.asyncio
async def test_actor_can_approve_gm_or_opm_denies_non_holder(engine_db_session):
    """Negative guard: a user who holds NEITHER gm nor opm must still be denied
    (the fix must not degrade into 'anyone can approve')."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, gm_first, actor = await _seed_two_gm_holders(db, dept_id)
    intruder = User(id=uuid.uuid4(), role="requester", is_active=True)
    db.add(intruder)
    await db.flush()

    rm = await get_role_management(db)
    dept_gm_opm = await get_dept_gm_opm_mapping(db)
    fbp = {uuid.UUID(u) for u in rm.get("finance_bp_user_ids", [])}

    ok = await _actor_can_approve(
        db, "gm_or_opm", intruder.id, "requester", requester.id, rm, dept_gm_opm, fbp)
    assert not ok, "a non-holder must not be authorized to approve a gm_or_opm step"


async def _make_pa_at_gm_step(db, created_by, status="submitted"):
    """A PA whose (config) workflow is a single gm_or_opm step, sitting on it."""
    pa = PaymentApplication(
        id=uuid.uuid4(),
        pa_number=f"PA-MH-{uuid.uuid4().hex[:4]}",
        title="Multi-holder reject-gate test",
        status=status,
        approval_step_idx=0,
        payment_amount=Decimal("500.00"),
        vendor_name="Titan Power Ltd",
        currency="CAD",
        invoice_ids=[],
        po_id=None,           # → routing_uid falls back to created_by (the requester)
        created_by=created_by,
    )
    db.add(pa)
    await db.flush()
    return pa


@pytest.mark.asyncio
async def test_reject_denied_for_non_approver(engine_db_session):
    """reject/cancel must require the same authorization as approve — an actor who
    is not the current step's approver cannot reject the document."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, gm_first, actor = await _seed_two_gm_holders(db, dept_id)
    intruder = User(id=uuid.uuid4(), role="requester", is_active=True)
    db.add(intruder)
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={
        "pa": [{"id": "gm_or_opm", "role": "gm_or_opm", "label": "GM"}]}))
    await db.flush()
    pa = await _make_pa_at_gm_step(db, requester.id)

    with pytest.raises(ValueError, match="[Nn]ot authorized"):
        await execute_action(db, "pa", pa.id, "reject", intruder.id, "requester")


@pytest.mark.asyncio
async def test_reject_allowed_for_authorized_holder(engine_db_session):
    """The authorized gm holder (even a non-first one) CAN reject."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, gm_first, actor = await _seed_two_gm_holders(db, dept_id)
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={
        "pa": [{"id": "gm_or_opm", "role": "gm_or_opm", "label": "GM"}]}))
    await db.flush()
    pa = await _make_pa_at_gm_step(db, requester.id)

    result = await execute_action(db, "pa", pa.id, "reject", actor.id, "dept_manager")
    assert result.new_status == "cancelled", (
        "authorized holder's reject must go through (PA → cancelled)"
    )
