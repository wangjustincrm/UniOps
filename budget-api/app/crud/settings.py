"""CRUD for single-row budget_settings table."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import BudgetSettings
from app.schemas.settings import BudgetSettingsUpdate


async def get_settings(db: AsyncSession) -> BudgetSettings:
    """Returns the single settings row. Creates default if not present."""
    result = await db.execute(select(BudgetSettings).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        row = BudgetSettings()
        db.add(row)
        await db.flush()
        await db.refresh(row)
    return row


async def update_settings(
    db: AsyncSession, payload: BudgetSettingsUpdate, actor_id: uuid.UUID,
) -> BudgetSettings:
    row = await get_settings(db)
    if payload.max_factors_per_account is not None:
        row.max_factors_per_account = payload.max_factors_per_account
    row.updated_by = actor_id
    await db.flush()
    await db.refresh(row)
    return row
