"""`sales_log` (FR-OB-53): what happened, with enough context to find the cause."""
from __future__ import annotations

from typing import Any

from app.models.documents import LogEntry
from app.platform.actors import SYSTEM_ACTOR


async def write(session, level: str, step: str, message: str, *, run_id: int | None = None,
                connection_id: int | None = None, document_id: int | None = None,
                context: dict[str, Any] | None = None) -> None:
    """In the caller's unit of work (it commits with the unit's own writes)."""
    session.add(LogEntry(level=level, step=step, message=message[:4000], run_id=run_id,
                         connection_id=connection_id, document_id=document_id, context=context))
    await session.flush()


async def write_alone(uow_factory, level: str, step: str, message: str, **kw: Any) -> None:
    """In a unit of its own: for failures, whose unit was rolled back."""
    async with uow_factory(SYSTEM_ACTOR) as uow:
        await write(uow.session, level, step, message, **kw)
        await uow.commit()
