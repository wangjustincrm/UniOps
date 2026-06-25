"""CRUD for expense policy configuration (singleton)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.policy import ExpensePolicyConfig
from app.schemas.policy import ExpensePolicyUpdate


async def get_policy(db: AsyncSession) -> ExpensePolicyConfig:
    result = await db.execute(select(ExpensePolicyConfig).limit(1))
    policy = result.scalar_one_or_none()
    if not policy:
        policy = ExpensePolicyConfig()
        db.add(policy)
        await db.flush()
    return policy


async def update_policy(db: AsyncSession, data: ExpensePolicyUpdate) -> ExpensePolicyConfig:
    policy = await get_policy(db)
    for field, val in data.model_dump(exclude_none=True).items():
        setattr(policy, field, val)
    return policy
