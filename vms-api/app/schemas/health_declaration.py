"""Pydantic schemas for HealthDeclaration resource."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.visit import HealthDeclStatus


class HealthDeclarationCreate(BaseModel):
    """Payload for POST /visits/{id}/health-declaration.

    `questionnaire_data` is the full snapshot of questions + answers — keeping
    the questions in the row (not just refs) so admin-side template edits
    don't retroactively alter historical declarations (PRD VMS-AU-013).
    """
    questionnaire_data: dict
    result: HealthDeclStatus
    # base64 PNG of signature pad image
    signature: str | None = None


class HealthDeclarationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    visit_id: uuid.UUID
    visitor_id: uuid.UUID
    questionnaire_data: dict
    result: HealthDeclStatus
    signature: str | None
    created_at: datetime
    updated_at: datetime


class HealthDeclarationListItem(BaseModel):
    """One row in the standalone declarations browser — declaration + the
    visit/visitor context needed to render the list and the view modal."""
    id: uuid.UUID
    visit_id: uuid.UUID
    visitor_id: uuid.UUID
    visitor_name: str
    company: str
    visit_date: date
    host_name: str
    access_area: str
    result: HealthDeclStatus
    questionnaire_data: dict
    signature: str | None
    created_at: datetime
    updated_at: datetime


class HealthDeclarationListResponse(BaseModel):
    items: list[HealthDeclarationListItem]
    total: int
