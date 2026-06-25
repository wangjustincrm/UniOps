"""Audit log read paths.

Writes go through `app.crud.audit.log_event` (PRD §2.5.2 VMS-AU-001..003).
Reads live here — filtered list with pagination and unbounded streaming
for CSV export. Both are read-only by design; the DB-level REVOKE on
`vms_audit_logs` (migration 0002) makes mutations impossible.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


def _apply_filters(stmt, *, user_id, action_type, entity_type, entity_id, date_from, date_to):
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action_type:
        stmt = stmt.where(AuditLog.action_type == action_type)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if date_from is not None:
        stmt = stmt.where(AuditLog.timestamp >= date_from)
    if date_to is not None:
        stmt = stmt.where(AuditLog.timestamp <= date_to)
    return stmt


async def list_audit_logs(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    action_type: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AuditLog], int]:
    base = select(AuditLog).order_by(AuditLog.id.desc())
    base = _apply_filters(
        base,
        user_id=user_id, action_type=action_type, entity_type=entity_type,
        entity_id=entity_id, date_from=date_from, date_to=date_to,
    )
    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = list((
        await db.execute(base.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all())
    return rows, total


async def stream_audit_logs(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    action_type: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    chunk: int = 500,
) -> AsyncIterator[AuditLog]:
    """Yield rows for CSV export. Page-cursored to avoid loading everything."""
    offset = 0
    base = select(AuditLog).order_by(AuditLog.id.asc())  # chronological for export
    base = _apply_filters(
        base,
        user_id=user_id, action_type=action_type, entity_type=entity_type,
        entity_id=entity_id, date_from=date_from, date_to=date_to,
    )
    while True:
        page_rows = list((
            await db.execute(base.offset(offset).limit(chunk))
        ).scalars().all())
        if not page_rows:
            return
        for r in page_rows:
            yield r
        if len(page_rows) < chunk:
            return
        offset += chunk
