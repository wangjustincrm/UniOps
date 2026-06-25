"""Cascade helpers for OA (expense-api) admin module.

Deletes polymorphic workflow references (tasks, approval_events) by document_id.
Both TaskMirror and ApprovalEventMirror have a document_id column.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_mirror import TaskMirror
from app.models.approval_event_mirror import ApprovalEventMirror


async def _count_by_document_id(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.document_id == document_id)
    return int((await db.execute(stmt)).scalar_one())


async def _delete_by_document_id(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    n = await _count_by_document_id(db, model, document_id)
    await db.execute(delete(model).where(model.document_id == document_id))
    return n


async def purge_shared_refs(db: AsyncSession, document_id: uuid.UUID) -> dict[str, int]:
    """Delete tasks and approval_events that reference a document by document_id.

    Returns counts for the cascade summary.
    """
    tasks = await _delete_by_document_id(db, TaskMirror, document_id)
    events = await _delete_by_document_id(db, ApprovalEventMirror, document_id)
    return {"tasks": tasks, "approval_events": events}
