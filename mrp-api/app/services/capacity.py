"""Capacity rule resolution (Phase 1B Task 1).

`resolve_effective_rules` is what the (later) MPS algorithm calls to find
out how much factory capacity applies to a given planning month — active
rules only, filtered to those whose [effective_from, effective_to] window
covers the first day of that month. `effective_to=None` means open-ended.
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capacity import MrpCapacityRule


def _month_first_day(month: str) -> date:
    y, m = month.split("-")
    return date(int(y), int(m), 1)


async def resolve_effective_rules(db: AsyncSession, on_month: str) -> list[MrpCapacityRule]:
    """Active rules whose [effective_from, effective_to] window covers the
    first day of `on_month` ('YYYY-MM'). effective_to NULL = open-ended."""
    d = _month_first_day(on_month)
    rows = (await db.execute(select(MrpCapacityRule).where(MrpCapacityRule.is_active.is_(True)))).scalars().all()
    return [r for r in rows if r.effective_from <= d and (r.effective_to is None or r.effective_to >= d)]
