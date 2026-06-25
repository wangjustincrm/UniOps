"""Custom form definitions (CFM) — PRD-OA §10.1.

Migrated out of expense_policy_config.custom_forms (JSONB) into a dedicated
table so each custom form is a first-class, individually addressable record
managed via /api/v1/expenses/custom-forms (PRD §12).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CustomFormDefinition(Base):
    __tablename__ = "custom_form_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_key: Mapped[str] = mapped_column(String(50), nullable=False, default="cfm")
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CAD")
    # Ordered list of field definitions (CfmFieldDef): name/label/field_type/required/options/placeholder
    field_schema: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
