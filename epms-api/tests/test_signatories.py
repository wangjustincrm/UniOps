"""Unit tests for app/crud/signatories.py — the single place PDF names come from.

Approval events are seeded directly rather than driven through POST /pr/{id}/action:
that endpoint delegates to approval-api, which is not reachable from a host-run
test suite (see tests/test_pr.py's pre-existing failures).
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.crud.gr import _actor_name
from app.crud.signatories import approval_signatories, gr_signatories, resolve_user_names
from app.models.approval import ApprovalEvent
from app.models.gr import GoodsReceipt
from app.models.po import PurchaseOrder
from app.models.pr import PrLineItem, PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _user(db: AsyncSession, full_name: str):
    u = await user_crud.create(db, RegisterRequest(
        email=f"sig-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name=full_name, role="requester"))
    await db.flush()
    return u


async def _pr(db: AsyncSession, created_by: uuid.UUID) -> PurchaseRequest:
    pr = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="Signatory PR", type=2,
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
async def test_approval_signatories_skips_auto_skipped_steps(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rita Requester")
        manager = await _user(db, "Manny Manager")
        gm = await _user(db, "Gina General")
        pr = await _pr(db, requester.id)
        # step 1 has no real approver: approval-api records the submitter as actor
        db.add_all([
            _event(pr, 0, manager.id, "dept_manager"),
            _event(pr, 1, requester.id, "director",
                   comment="Auto-skipped (department has no Director)"),
            _event(pr, 2, gm.id, "gm_or_opm"),
        ])
        await db.commit()

        name, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)

        assert name == "Rita Requester"
        assert [a["name"] for a in approvals] == ["Manny Manager", "Gina General"]
        assert [a["role"] for a in approvals] == ["Dept Manager", "GM / OPM"]


@pytest.mark.asyncio
async def test_approval_signatories_keeps_auto_approved_dual_role(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await _user(db, "Rhonda Requester")
        dual = await _user(db, "Dana Dualrole")
        pr = await _pr(db, requester.id)
        db.add_all([
            _event(pr, 0, dual.id, "dept_manager"),
            _event(pr, 1, dual.id, "finance_bp",
                   comment="Auto-approved (same approver holds both roles)"),
        ])
        await db.commit()

        _, approvals = await approval_signatories(db, "pr", pr.id, pr.created_by)

        assert [(a["role"], a["name"]) for a in approvals] == [
            ("Dept Manager", "Dana Dualrole"),
            ("Finance BP", "Dana Dualrole"),
        ]


@pytest.mark.asyncio
async def test_gr_signatories_resolves_uuid_shaped_names(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        creator = await _user(db, "Wanda Warehouse")
        acker = await _user(db, "Aaron Acknowledger")
        vendor = Vendor(
            code=f"V{uuid.uuid4().hex[:6]}", name="Sig Vendor", category="general",
            contact_name="Vic Vendor", contact_email="vic@sigvendor.com",
        )
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(
            number=f"PO-{uuid.uuid4().hex[:8]}", title="Sig PO", type=2,
            status="issued", vendor_id=vendor.id, vendor_name=vendor.name,
            currency="CAD", subtotal=Decimal("0"), total=Decimal("0"),
            created_by=creator.id,
        )
        db.add(po)
        await db.flush()
        gr = GoodsReceipt(
            number=f"GR-{uuid.uuid4().hex[:8]}", title="Sig GR",
            po_id=po.id, po_number=po.number, vendor_id=vendor.id,
            vendor_name=vendor.name, gr_type="physical", procurement_type=2,
            currency="CAD", status="collection_pending",
            received_by="Wanda Warehouse",          # browser path: a real name
            acknowledged_by=str(acker.id),          # non-browser path: a raw UUID
            created_by=creator.id,
        )
        db.add(gr)
        await db.commit()

        sig = await gr_signatories(db, gr)

        assert sig["created_by_name"] == "Wanda Warehouse"
        assert sig["received_by"] == "Wanda Warehouse"
        assert sig["acknowledged_by"] == "Aaron Acknowledger"   # UUID resolved to a name


@pytest.mark.asyncio
async def test_resolve_user_names_ignores_none_and_unknown(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        known = await _user(db, "Ken Known")
        await db.commit()

        names = await resolve_user_names(db, [known.id, None, uuid.uuid4()])

        assert names == {known.id: "Ken Known"}


@pytest.mark.asyncio
async def test_actor_name_prefers_full_name_over_uuid(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await _user(db, "Nina Named")
        await db.commit()

        assert await _actor_name(db, user.id) == "Nina Named"


@pytest.mark.asyncio
async def test_actor_name_falls_back_to_id_when_user_is_gone(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        missing = uuid.uuid4()

        assert await _actor_name(db, missing) == str(missing)
