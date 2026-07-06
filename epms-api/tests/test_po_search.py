"""Regression: PO list search must match vendor name + budget code, not just
title + number (same defect as the PR list search)."""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import po as po_crud
from app.crud import user as user_crud
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _seed(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"posearch-{uuid.uuid4().hex[:6]}@t.com", password="TestPass1!",
            full_name="PO Search", role="system_admin"))
        await db.commit()
        vendor = Vendor(
            code=f"V-{uuid.uuid4().hex[:6]}", name="Summa Strategies Canada Inc.",
            category="Services", contact_name="S", contact_email="s@summa.test")
        db.add(vendor)
        await db.commit()
        await db.refresh(vendor)
        po = PurchaseOrder(
            number=f"PO-{uuid.uuid4().hex[:6]}", title="Consulting engagement", type=4,
            vendor_id=vendor.id, vendor_name="Summa Strategies Canada Inc.",
            budget_code="BC-PO-1", created_by=user.id)
        db.add(po)
        await db.commit()
        return str(po.id)


async def _search(test_engine, term):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        items, _ = await po_crud.get_all(db, search=term)
        return [str(p.id) for p in items]


@pytest.mark.asyncio
async def test_po_search_matches_vendor_name(test_engine):
    po_id = await _seed(test_engine)
    assert po_id in await _search(test_engine, "Summa")


@pytest.mark.asyncio
async def test_po_search_matches_budget_code(test_engine):
    po_id = await _seed(test_engine)
    assert po_id in await _search(test_engine, "BC-PO")


@pytest.mark.asyncio
async def test_po_search_excludes_nonmatch(test_engine):
    po_id = await _seed(test_engine)
    assert po_id not in await _search(test_engine, f"nomatch-{uuid.uuid4().hex}")
