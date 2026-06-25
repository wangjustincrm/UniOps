"""Cascade helpers for the VMS (vms-api) admin module.

Purges the shared `tasks` table (mirrored from epms-api, model `Task` in
app/models/task_mirror.py) by `document_id`. VMS has no shared
approval_events mirror, so only tasks are purged here.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_mirror import Task


async def _count_by_document_id(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.document_id == document_id)
    return int((await db.execute(stmt)).scalar_one())


async def _delete_by_document_id(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    n = await _count_by_document_id(db, model, document_id)
    await db.execute(delete(model).where(model.document_id == document_id))
    return n


async def purge_shared_refs(db: AsyncSession, document_id: uuid.UUID) -> dict[str, int]:
    """Delete `tasks` rows that reference a document by document_id.

    Returns counts for the cascade summary.
    """
    tasks = await _delete_by_document_id(db, Task, document_id)
    return {"tasks": tasks}
