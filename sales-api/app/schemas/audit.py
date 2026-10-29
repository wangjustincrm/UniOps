"""One entry of a record's audit history (sales_audit)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    user_name: str
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
