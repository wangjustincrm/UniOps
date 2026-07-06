"""Regression: PR list search must match vendor name (both the denormalized
snapshot column AND the live Vendor.name via vendor_id) and budget code —
previously it only matched title + number, so searching by vendor returned
nothing (e.g. imported PRs with a free-text vendor_name).
"""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import pr as pr_crud
from app.crud import user as user_crud
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _seed(test_engine):
    """Seed two PRs: one carrying a denormalized vendor_name snapshot (no
    vendor_id, import-style), one linked to a live Vendor row. Returns their ids."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"prsearch-{uuid.uuid4().hex[:6]}@t.com", password="TestPass1!",
            full_name="PR Search", role="system_admin"))
        await db.commit()
        uid = user.id

        vendor = Vendor(
            code=f"V-{uuid.uuid4().hex[:6]}", name="Zephyr Supplies Co",
            category="Services", contact_name="Z", contact_email="z@zephyr.test")
        db.add(vendor)
        await db.commit()
        await db.refresh(vendor)

        pr_snapshot = PurchaseRequest(
            number=f"PR-SNAP-{uuid.uuid4().hex[:6]}", title="Office chairs", type=3,
            created_by=uid, vendor_name="Summa Strategies Canada Inc.", budget_code="BC-TEST-1")
        pr_live = PurchaseRequest(
            number=f"PR-LIVE-{uuid.uuid4().hex[:6]}", title="Network cables", type=3,
            created_by=uid, vendor_id=vendor.id)
        db.add_all([pr_snapshot, pr_live])
        await db.commit()
        return str(pr_snapshot.id), str(pr_live.id)


async def _search(test_engine, term):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        items, _ = await pr_crud.get_all(db, search=term)
        return [str(p.id) for p in items]


@pytest.mark.asyncio
async def test_search_matches_denormalized_vendor_name(test_engine):
    snap_id, _ = await _seed(test_engine)
    ids = await _search(test_engine, "Summa")
    assert snap_id in ids, "search 'Summa' must match the denormalized vendor_name snapshot"


@pytest.mark.asyncio
async def test_search_matches_live_vendor_name_via_vendor_id(test_engine):
    _, live_id = await _seed(test_engine)
    ids = await _search(test_engine, "Zephyr")
    assert live_id in ids, "search must match the live Vendor.name via vendor_id subquery"


@pytest.mark.asyncio
async def test_search_matches_budget_code(test_engine):
    snap_id, _ = await _seed(test_engine)
    ids = await _search(test_engine, "BC-TEST")
    assert snap_id in ids, "search must match budget_code"


@pytest.mark.asyncio
async def test_search_excludes_nonmatch(test_engine):
    snap_id, live_id = await _seed(test_engine)
    ids = await _search(test_engine, f"nomatch-{uuid.uuid4().hex}")
    assert snap_id not in ids and live_id not in ids
