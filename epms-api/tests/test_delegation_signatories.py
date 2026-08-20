"""Unit tests for the delegation-name rendering in app/crud/signatories.py.

The approval-api engine already appends "on behalf of <delegator>" to an
approval event's comment when a stand-in approves (see engine.py). These
tests confirm approval_signatories() reads that text back out and renders
"<delegate> (on behalf of <delegator>)" on the PDF signature line, without
disturbing the existing machine-approval filtering.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.crud.signatories import approval_signatories
from app.models.approval import ApprovalEvent
from app.models.pr import PrLineItem, PurchaseRequest
from app.schemas.auth import RegisterRequest


async def _user(db: AsyncSession, full_name: str):
    u = await user_crud.create(db, RegisterRequest(
        email=f"delsig-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name=full_name, role="requester"))
    await db.flush()
    return u


async def _pr(db: AsyncSession, created_by: uuid.UUID) -> PurchaseRequest:
    pr = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="Delegation Signatory PR", type=2,
        status="approved", amount=Decimal("150.00"), currency="CAD",
        created_by=created_by,
        line_items=[PrLineItem(
            description="Widget", qty=Decimal("3"), unit="ea",
            unit_price=Decimal("50.00"), line_total=Decimal("150.00"), sort_order=0)],
    )
    db.add(pr)
    await db.flush()
    return pr


def _event(pr: PurchaseRequest, step: int, actor_id: uuid.UUID, role: str, comment=None):
    return ApprovalEvent(
        document_type="pr", document_id=pr.id, document_number=pr.number,
        step_idx=step, action="approve", actor_id=actor_id,
        actor_role=role, comment=comment,
    )


@pytest.mark.asyncio
async def test_delegated_approval_renders_on_behalf_of_suffix(test_engine):
    """An approval event whose comment ends "on behalf of Sivers" renders the
    signatory name as "Mohammadi (on behalf of Sivers)"."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rita Requester")
        delegate = await _user(db, "Mohammadi")
        pr = await _pr(db, requester.id)
        db.add_all([
            _event(pr, 0, delegate.id, "dept_manager", comment="on behalf of Sivers"),
        ])
        await db.commit()

        _, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)

        assert [a["name"] for a in approvals] == ["Mohammadi (on behalf of Sivers)"]


@pytest.mark.asyncio
async def test_delegated_approval_with_own_comment_renders_on_behalf_of_suffix(test_engine):
    """The user's own comment is preserved before the em-dash; only the
    delegator's name after the marker is pulled onto the signature line."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rita Requester")
        delegate = await _user(db, "Mohammadi")
        pr = await _pr(db, requester.id)
        db.add_all([
            _event(pr, 0, delegate.id, "dept_manager",
                   comment="Looks good — on behalf of Sivers"),
        ])
        await db.commit()

        _, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)

        assert [a["name"] for a in approvals] == ["Mohammadi (on behalf of Sivers)"]


@pytest.mark.asyncio
async def test_plain_approval_renders_just_the_name(test_engine):
    """A plain approval with no delegation renders exactly as before — just
    the name, no suffix, no empty parentheses."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rita Requester")
        manager = await _user(db, "Manny Manager")
        pr = await _pr(db, requester.id)
        db.add_all([
            _event(pr, 0, manager.id, "dept_manager"),
        ])
        await db.commit()

        _, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)

        assert [a["name"] for a in approvals] == ["Manny Manager"]


@pytest.mark.asyncio
async def test_machine_events_still_filtered_but_dual_role_still_kept(test_engine):
    """Machine events are still filtered out, and "Auto-approved (same
    approver holds both roles)" is still KEPT — it is a real person holding
    two posts. The "on behalf of" marker must not disturb this filtering:
    the PMS-migration marker "Auto-approved on behalf of <role> — PMS
    migration" is filtered before delegation-name rendering ever runs."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rita Requester")
        admin = await _user(db, "System Administrator")
        dual = await _user(db, "Dana Dualrole")
        pr = await _pr(db, requester.id)
        db.add_all([
            _event(pr, 0, admin.id, "finance_manager",
                   comment="Auto-approved on behalf of Finance Manager — PMS migration "
                            "(PMS status APPROVED)"),
            _event(pr, 1, dual.id, "dept_manager"),
            _event(pr, 2, dual.id, "finance_bp",
                   comment="Auto-approved (same approver holds both roles)"),
        ])
        await db.commit()

        _, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)

        assert [a["name"] for a in approvals] == ["Dana Dualrole", "Dana Dualrole"]
        assert [a["role"] for a in approvals] == ["Dept Manager", "Finance BP"]
