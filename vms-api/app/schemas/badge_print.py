"""Pydantic schemas for BadgePrint resource + badge template management."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BadgePrintCreate(BaseModel):
    """Payload for POST /visits/{id}/print-badge.

    First print on a visit performs check-in atomically. Subsequent prints
    require a `reprint_reason` (PRD VMS-LB-008).
    """
    template_used: str = "standard"
    reprint_reason: str | None = Field(default=None, max_length=500)


class BadgePrintResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    visit_id: uuid.UUID
    printed_by: uuid.UUID
    printed_at: datetime
    reprint_reason: str | None
    template_used: str


class BadgeTemplate(BaseModel):
    """One entry of the `vms_config.badge_templates` JSONB map."""
    name: str
    html: str
    css: str
    is_default: bool = False
