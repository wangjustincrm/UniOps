"""Pydantic schemas for Task inbox."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class TaskListResponse(BaseModel):
    items: list["TaskResponse"]
    total: int


class TaskResponse(BaseModel):
    id: uuid.UUID
    type: str
    priority: str
    document_type: str
    document_id: uuid.UUID
    document_number: str
    assigned_role: str
    assigned_user_id: uuid.UUID | None
    title: str
    description: str | None
    due_date: date | None
    amount: Decimal | None
    vendor: str | None
    is_completed: bool
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
