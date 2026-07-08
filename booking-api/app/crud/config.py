"""CRUD for BookingConfig — singleton row pattern."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking_config import BookingConfig, DEFAULT_RULES


async def get_or_create_config(db: AsyncSession) -> BookingConfig:
    """Return the singleton BookingConfig row, creating it with defaults if absent."""
    result = await db.execute(select(BookingConfig).limit(1))
    config = result.scalar_one_or_none()
    if config is None:
        config = BookingConfig(
            smtp_settings={},
            rules=dict(DEFAULT_RULES),
            organizer_mode="system",
        )
        db.add(config)
        await db.flush()
        await db.refresh(config)
    return config
