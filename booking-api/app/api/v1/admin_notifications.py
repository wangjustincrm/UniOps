"""Admin endpoints for notification log oversight and manual resend.

GET  /admin/notifications          — list + filter NotificationLog rows
POST /admin/notifications/{id}/resend — re-run send_notification for a log

AdminUser permission required (manage_meeting_rooms).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.core.deps import SessionDep
from app.core.permissions import AdminUser
from app.models.notification import NotificationLog
from app.schemas.notification import NotificationListOut, NotificationLogOut
from app.services.notifications import send_notification

router = APIRouter()


@router.get("", response_model=NotificationListOut)
async def list_notifications(
    db: SessionDep,
    current_user: AdminUser,
    notif_status: str | None = Query(default=None, alias="status"),
    notif_type: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> NotificationListOut:
    """List NotificationLog rows with optional status/type filters."""
    clauses = []
    if notif_status is not None:
        clauses.append(NotificationLog.status == notif_status)
    if notif_type is not None:
        clauses.append(NotificationLog.notif_type == notif_type)

    count_stmt = select(func.count()).select_from(NotificationLog)
    data_stmt = (
        select(NotificationLog)
        .order_by(NotificationLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if clauses:
        from sqlalchemy import and_
        count_stmt = count_stmt.where(and_(*clauses))
        data_stmt = data_stmt.where(and_(*clauses))

    total: int = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(data_stmt)).scalars().all()

    return NotificationListOut(
        items=[NotificationLogOut.model_validate(r) for r in rows],
        total=total,
    )


@router.post("/{log_id}/resend", response_model=NotificationLogOut)
async def resend_notification(
    log_id: uuid.UUID,
    db: SessionDep,
    current_user: AdminUser,
) -> NotificationLogOut:
    """Manually resend a notification log entry (e.g. after fixing SMTP config).

    Re-runs send_notification and returns the updated log.
    """
    result = await db.execute(
        select(NotificationLog).where(NotificationLog.id == log_id)
    )
    log_entry = result.scalar_one_or_none()
    if log_entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"NotificationLog {log_id} not found",
        )

    await send_notification(db, log_entry)
    await db.flush()

    return NotificationLogOut.model_validate(log_entry)
