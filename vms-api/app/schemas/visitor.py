"""Pydantic schemas for Visitor resource."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.visitor import VisitorType


class VisitorCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name:  str = Field(min_length=1, max_length=100)
    # Optional — interviewees / students often have no company. Stored as ""
    # (the column is NOT NULL) when omitted.
    company_name: str = Field(default="", max_length=200)
    job_title: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    visitor_type: VisitorType = VisitorType.other
    id_verified: bool = False


class VisitorUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name:  str | None = Field(default=None, min_length=1, max_length=100)
    company_name: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=20)
    email: EmailStr | None = None
    visitor_type: VisitorType | None = None
    id_verified: bool | None = None


class VisitorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    company_name: str
    job_title: str | None
    phone: str | None
    email: str | None
    visitor_type: VisitorType
    id_verified: bool
    safety_training_confirmed_at: datetime | None = None
    safety_training_confirmed_by: uuid.UUID | None = None
    ppe_issued_at: datetime | None = None
    ppe_issued_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class VisitorListResponse(BaseModel):
    items: list[VisitorResponse]
    total: int
