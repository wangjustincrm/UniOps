"""Cascade helpers for finance-api admin module.

Deletes shared polymorphic workflow references (tasks) by document_id.
The Task mirror has a document_id column matched against the document's id.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mirrors import Task


async def _count_by_document_id(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.document_id == document_id)
    return int((await db.execute(stmt)).scalar_one())


async def purge_shared_refs(db: AsyncSession, document_id: uuid.UUID) -> dict[str, int]:
    """Delete tasks that reference a document by document_id. Returns counts."""
    tasks = await _count_by_document_id(db, Task, document_id)
    await db.execute(delete(Task).where(Task.document_id == document_id))
    return {"tasks": tasks}


async def count_shared_refs(db: AsyncSession, document_id: uuid.UUID) -> dict[str, int]:
    """Count tasks referencing a document (no mutation)."""
    return {"tasks": await _count_by_document_id(db, Task, document_id)}
