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


_RESOLVERS: dict[str, Resolver] = {
    "users": Resolver("users", _user_by_id, _user_search),
    "vendors": Resolver("vendors", _vendor_by_id, _vendor_search),
    "cost_centers": Resolver("cost_centers", _cc_by_id, _cc_search),
}


def get_resolver(source: str) -> Resolver:
    r = _RESOLVERS.get(source)
    if r is None:
        raise ValueError(f"Unknown reference source '{source}'")
    return r


def resolver_sources() -> set[str]:
    return set(_RESOLVERS)
