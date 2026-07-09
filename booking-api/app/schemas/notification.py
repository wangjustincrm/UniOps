"""Pydantic schemas for NotificationLog admin endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class NotificationLogOut(BaseModel):
    """Full read model for a NotificationLog row."""
    id: uuid.UUID
    booking_id: uuid.UUID | None
    notif_type: str
    recipients: list[Any]
    status: str
    error: str | None
    retry_count: int
    sent_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListOut(BaseModel):
    """Paginated list response."""
    items: list[NotificationLogOut]
    total: int
