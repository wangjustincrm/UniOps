"""Generic cascade helpers shared by entity cascade handlers.

Tasks and approval_events reference a document polymorphically via
(document_type, document_id). We match by document_id ONLY: document_id is the
referenced row's unique primary key, so it identifies the document regardless of
which document_type string the producer used. This matters because the same
document can spawn tasks under several type tags — e.g. a payment application
yields document_type "pa" for PO-based PAs but "pa_dir" for Direct PAs. Matching
on document_id alone purges every related task/event, avoiding orphaned Task
Inbox rows after a delete.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalEvent
from app.models.task import Task


async def count_polymorphic(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.document_id == document_id)
    return int((await db.execute(stmt)).scalar_one())


async def delete_polymorphic(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    n = await count_polymorphic(db, model, document_id)
    await db.execute(delete(model).where(model.document_id == document_id))
    return n


async def purge_workflow_refs(db: AsyncSession, document_id: uuid.UUID) -> dict[str, int]:
    """Delete tasks + approval_events that reference a document (by document_id).
    Returns counts for the cascade summary."""
    tasks = await delete_polymorphic(db, Task, document_id)
    events = await delete_polymorphic(db, ApprovalEvent, document_id)
    return {"tasks": tasks, "approval_events": events}
