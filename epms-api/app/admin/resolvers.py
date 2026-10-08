# epms-api/app/admin/resolvers.py
"""Reference-field resolvers: map a ref_source key to how we search/fetch the
target row and render its display label. Backed by the existing ORM models on the
shared DB."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.user import User
from app.models.vendor import Vendor


@dataclass
class RefHit:
    id: uuid.UUID
    label: str


@dataclass
class Resolver:
    source: str
    fetch_by_id: Callable[[AsyncSession, uuid.UUID], Awaitable["RefHit | None"]]
    search: Callable[[AsyncSession, str, int], Awaitable[list["RefHit"]]]


def _user_label(u: User) -> str:
    return f"{u.full_name} <{u.email}>" if u.full_name else u.email


async def _user_by_id(db: AsyncSession, rid: uuid.UUID) -> RefHit | None:
    u = (await db.execute(select(User).where(User.id == rid))).scalar_one_or_none()
    return RefHit(u.id, _user_label(u)) if u else None


async def _user_search(db: AsyncSession, q: str, limit: int) -> list[RefHit]:
    stmt = select(User)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(User.full_name.ilike(term), User.email.ilike(term)))
    stmt = stmt.order_by(User.full_name.asc()).limit(limit)
    return [RefHit(u.id, _user_label(u)) for u in (await db.execute(stmt)).scalars().all()]


async def _vendor_by_id(db: AsyncSession, rid: uuid.UUID) -> RefHit | None:
    v = (await db.execute(select(Vendor).where(Vendor.id == rid))).scalar_one_or_none()
    return RefHit(v.id, v.name) if v else None


async def _vendor_search(db: AsyncSession, q: str, limit: int) -> list[RefHit]:
    stmt = select(Vendor)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(Vendor.name.ilike(term), Vendor.code.ilike(term)))
    stmt = stmt.order_by(Vendor.name.asc()).limit(limit)
    return [RefHit(v.id, v.name) for v in (await db.execute(stmt)).scalars().all()]


def _cc_label(c: CostCenter) -> str:
    return f"{c.code} — {c.name}"


async def _cc_by_id(db: AsyncSession, rid: uuid.UUID) -> RefHit | None:
    c = (await db.execute(select(CostCenter).where(CostCenter.id == rid))).scalar_one_or_none()
    return RefHit(c.id, _cc_label(c)) if c else None


async def _cc_search(db: AsyncSession, q: str, limit: int) -> list[RefHit]:
    stmt = select(CostCenter)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(CostCenter.name.ilike(term), CostCenter.code.ilike(term)))
    stmt = stmt.order_by(CostCenter.code.asc()).limit(limit)
    return [RefHit(c.id, _cc_label(c)) for c in (await db.execute(stmt)).scalars().all()]


def _dept_label(d: Department) -> str:
    return f"{d.code} — {d.name}"


async def _dept_by_id(db: AsyncSession, rid: uuid.UUID) -> RefHit | None:
    d = (await db.execute(select(Department).where(Department.id == rid))).scalar_one_or_none()
    return RefHit(d.id, _dept_label(d)) if d else None


async def _dept_search(db: AsyncSession, q: str, limit: int) -> list[RefHit]:
    stmt = select(Department)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(Department.name.ilike(term), Department.code.ilike(term)))
    stmt = stmt.order_by(Department.code.asc()).limit(limit)
    return [RefHit(d.id, _dept_label(d)) for d in (await db.execute(stmt)).scalars().all()]


def _invoice_label(i: Invoice) -> str:
    """Shown in the picker. The vendor's own number is what an operator matches
    against a paper invoice, so it leads; the internal ref disambiguates."""
    vendor_no = (i.vendor_invoice_number or "").strip() or "no vendor no."
    return f"{i.internal_ref} · #{vendor_no} · {i.vendor_name} · {i.total_amount} {i.currency} [{i.status}]"


async def _invoice_by_id(db: AsyncSession, rid: uuid.UUID) -> RefHit | None:
    row = (await db.execute(select(Invoice).where(Invoice.id == rid))).scalar_one_or_none()
    return None if row is None else RefHit(row.id, _invoice_label(row))


async def _invoice_search(db: AsyncSession, q: str, limit: int) -> list[RefHit]:
    stmt = select(Invoice)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(Invoice.internal_ref.ilike(term),
                              Invoice.vendor_invoice_number.ilike(term),
                              Invoice.po_number.ilike(term),
                              Invoice.vendor_name.ilike(term)))
    stmt = stmt.order_by(Invoice.created_at.desc()).limit(limit)
    return [RefHit(i.id, _invoice_label(i)) for i in (await db.execute(stmt)).scalars().all()]


def _gr_label(g: GoodsReceipt) -> str:
    return f"{g.number} · {g.po_number or 'no PO'} · [{g.status}]"


async def _gr_by_id(db: AsyncSession, rid: uuid.UUID) -> RefHit | None:
    row = (await db.execute(select(GoodsReceipt).where(GoodsReceipt.id == rid))).scalar_one_or_none()
    return None if row is None else RefHit(row.id, _gr_label(row))


async def _gr_search(db: AsyncSession, q: str, limit: int) -> list[RefHit]:
    stmt = select(GoodsReceipt)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(GoodsReceipt.number.ilike(term),
                              GoodsReceipt.po_number.ilike(term),
                              GoodsReceipt.title.ilike(term)))
    stmt = stmt.order_by(GoodsReceipt.created_at.desc()).limit(limit)
    return [RefHit(g.id, _gr_label(g)) for g in (await db.execute(stmt)).scalars().all()]


_RESOLVERS: dict[str, Resolver] = {
    "users": Resolver("users", _user_by_id, _user_search),
    "vendors": Resolver("vendors", _vendor_by_id, _vendor_search),
    "cost_centers": Resolver("cost_centers", _cc_by_id, _cc_search),
    # purchase_agreements.department_id drives approval routing
    # (engine._routing_department_id reads the agreement's OWN department, not the
    # submitter's) and the agreement has no denormalized department_name column, so
    # without this the admin can only paste a raw UUID and hope.
    "departments": Resolver("departments", _dept_by_id, _dept_search),
    # payment_applications.invoice_ids / gr_ids are JSONB arrays of ids with no FK
    # behind them, and they are the ONLY record of what a PA settles — finance-api's
    # payment executor, the remittance advice and the create-PA screen's
    # already-claimed lock all read them. A PA approved with an empty array cannot
    # be repaired anywhere else (pa.py's update_pa accepts draft/returned only), so
    # Data Maintenance is the repair path and these two resolvers are what make the
    # ids pickable instead of pasted.
    "invoices": Resolver("invoices", _invoice_by_id, _invoice_search),
    "grs": Resolver("grs", _gr_by_id, _gr_search),
}


def get_resolver(source: str) -> Resolver:
    r = _RESOLVERS.get(source)
    if r is None:
        raise ValueError(f"Unknown reference source '{source}'")
    return r


def resolver_sources() -> set[str]:
    return set(_RESOLVERS)
