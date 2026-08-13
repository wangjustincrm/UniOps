"""Intent product lifecycle (design §5.3, decisions D7/D8/D11)."""
import secrets
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.demand_series import MrpDemandSeries, MrpForecastChangeLog

INTENT_CODE_PREFIX = "INTENT-"


def generate_intent_code() -> str:
    """`INTENT-` + 8 lowercase hex chars.

    Random rather than sequential so two planners creating a row at the same
    moment never collide on a counter; the DB unique index is the backstop.
    """
    return f"{INTENT_CODE_PREFIX}{secrets.token_hex(4)}"


def is_intent_code(code: str) -> bool:
    return code.startswith(INTENT_CODE_PREFIX)


class IntentBindConflict(Exception):
    """Raised when the target material code already carries forecast rows.

    Business states this cannot happen (intent products are SKUs still being
    planned), so decision D11 makes it a hard rejection rather than a silent
    merge: if the impossible happens, a planner must look at it.
    """


async def bind_intent_to_material(
    db: AsyncSession, *, intent_code: str, material_code: str, actor_name: str | None,
) -> tuple[int, Decimal]:
    """Move every series cell and change-log row from the placeholder code to
    the real material code, in one transaction. Returns (months, total_qty)."""
    clash = (await db.execute(
        select(func.count()).select_from(MrpDemandSeries)
        .where(MrpDemandSeries.material_code == material_code)
    )).scalar_one()
    if clash:
        raise IntentBindConflict(material_code)

    rows = (await db.execute(
        select(MrpDemandSeries.month, MrpDemandSeries.qty)
        .where(MrpDemandSeries.material_code == intent_code)
    )).all()
    moved_months = len(rows)
    moved_qty = sum((Decimal(str(q)) for _, q in rows), Decimal("0"))

    await db.execute(update(MrpDemandSeries)
                     .where(MrpDemandSeries.material_code == intent_code)
                     .values(material_code=material_code))
    await db.execute(update(MrpForecastChangeLog)
                     .where(MrpForecastChangeLog.material_code == intent_code)
                     .values(material_code=material_code))

    for month, qty in rows:
        db.add(MrpForecastChangeLog(
            material_code=material_code, month=month,
            old_qty=qty, new_qty=qty,
            source="intent_bind", changed_by_name=actor_name,
        ))
    return moved_months, moved_qty
