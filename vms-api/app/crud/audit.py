"""Audit-log writer (PRD §2.5.2 VMS-AU-001..003).

Every VMS mutation goes through `log_event()`. Rows are DB-level immutable
(migration 0002 revokes UPDATE/DELETE) — only INSERT is permitted to the
application role, so this module is the only place that needs to write.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


def _coerce(value: Any) -> Any:
    """Make a value JSON-safe (no Decimal, no datetime, no UUID, no Enum)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    return str(value)


def snapshot(model: Any) -> dict[str, Any]:
    """Snapshot a SQLAlchemy model into a JSON-safe dict.

    Used for `old_value` / `new_value` columns. Internal-only fields (SQLA
    bookkeeping) are skipped via `__table__.columns`.
    """
    if model is None:
        return {}
    out: dict[str, Any] = {}
    for col in model.__table__.columns:
        out[col.name] = _coerce(getattr(model, col.name))
    return out


async def log_event(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    user_name: str,
    action_type: str,
    entity_type: str,
    entity_id: uuid.UUID,
    ip_address: str,
    user_agent: str | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    notes: str | None = None,
) -> AuditLog:
    """Append a single immutable audit-log row.

    The caller is expected to commit (or the surrounding request session will
    commit on success per `get_session`). We don't `await db.commit()` here so
    the audit write stays atomic with the business mutation it describes.
    """
    row = AuditLog(
        user_id=user_id,
        user_name=user_name,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address[:45],
        user_agent=(user_agent or None) and user_agent[:500],
        notes=notes,
    )
    db.add(row)
    await db.flush()
    return row
