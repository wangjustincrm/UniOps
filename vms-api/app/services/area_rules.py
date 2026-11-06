"""What each access zone requires — the one place these rules live.

Every rule that depends on the zone reads from here: approval routing
(services/approval.py), the badge-print health gate (crud/badge.py), the
training / PPE confirmation tasks (services/compliance.py), the GMP reports and
KPIs (services/reports.py), and — through GET /api/v1/area-rules — the VMS
frontend's hints and banners.

Zones are rows in `vms_zones` (VMS Admin → Zones); see app.models.zone. Two
settings drive everything:

  - `gmp_grade` → a passing health declaration for every visitor before the
    badge prints, training / PPE confirmation at check-in, and the zone counts
    in the GMP / Lab reports;
  - managers → the visit needs approval, and the workflow's Zone Manager step
    goes to them (any one may approve). No managers = no approval.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_mirror import User
from app.models.zone import Zone, ZoneManager


@dataclass(frozen=True)
class ZoneRules:
    code: str
    name: str
    gmp_grade: bool
    is_active: bool
    sort_order: int
    # Every configured manager, active or not — whether approval is required
    # depends on this list, not on who is active today (see requires_approval).
    manager_ids: tuple[uuid.UUID, ...] = field(default=())
    active_manager_ids: tuple[uuid.UUID, ...] = field(default=())

    @property
    def requires_approval(self) -> bool:
        # Configured managers, not active ones: a zone whose managers have all
        # left must stop the visit at submit (no one to route to), not quietly
        # book it without approval.
        return bool(self.manager_ids)

    @property
    def requires_health_declaration(self) -> bool:
        return self.gmp_grade

    @property
    def compliance_tasks_at_check_in(self) -> bool:
        return self.gmp_grade

    def as_dict(self, manager_names: dict[uuid.UUID, str] | None = None) -> dict:
        names = manager_names or {}
        return {
            "area": self.code,
            "name": self.name,
            "is_active": self.is_active,
            "sort_order": self.sort_order,
            "gmp_grade": self.gmp_grade,
            "requires_approval": self.requires_approval,
            "requires_health_declaration": self.requires_health_declaration,
            "compliance_tasks_at_check_in": self.compliance_tasks_at_check_in,
            "managers": [
                {"id": str(u), "name": names.get(u, "")} for u in self.manager_ids
            ],
        }


async def _load(db: AsyncSession, codes: list[str] | None = None) -> list[ZoneRules]:
    stmt = select(Zone).order_by(Zone.sort_order, Zone.name)
    if codes is not None:
        stmt = stmt.where(Zone.code.in_(codes))
    zones = (await db.execute(stmt)).scalars().all()
    if not zones:
        return []
    rows = (await db.execute(
        select(ZoneManager.zone_id, ZoneManager.user_id, User.is_active)
        .join(User, User.id == ZoneManager.user_id)
        .where(ZoneManager.zone_id.in_([z.id for z in zones]))
        .order_by(ZoneManager.created_at, ZoneManager.user_id)
    )).all()
    managers: dict[uuid.UUID, list[uuid.UUID]] = {}
    active: dict[uuid.UUID, list[uuid.UUID]] = {}
    for zone_id, user_id, is_active in rows:
        managers.setdefault(zone_id, []).append(user_id)
        if is_active:
            active.setdefault(zone_id, []).append(user_id)
    return [
        ZoneRules(
            code=z.code, name=z.name, gmp_grade=z.gmp_grade, is_active=z.is_active,
            sort_order=z.sort_order,
            manager_ids=tuple(managers.get(z.id, ())),
            active_manager_ids=tuple(active.get(z.id, ())),
        )
        for z in zones
    ]


async def rules_for(db: AsyncSession, code: str) -> ZoneRules | None:
    found = await _load(db, [code])
    return found[0] if found else None


async def all_rules(db: AsyncSession) -> list[ZoneRules]:
    return await _load(db)


async def gmp_grade_codes(db: AsyncSession) -> list[str]:
    """Codes of every GMP-grade zone, retired ones included — reports over a
    past window must still count visits to a zone retired since."""
    return list((await db.execute(
        select(Zone.code).where(Zone.gmp_grade.is_(True)).order_by(Zone.sort_order, Zone.name)
    )).scalars().all())


async def is_gmp_grade(db: AsyncSession, code: str) -> bool:
    return bool((await db.execute(
        select(Zone.gmp_grade).where(Zone.code == code)
    )).scalar_one_or_none())


async def zone_label(db: AsyncSession, code: str) -> str:
    """The zone's display name, for emails / task text. Falls back to the code."""
    name = (await db.execute(select(Zone.name).where(Zone.code == code))).scalar_one_or_none()
    return name or code
