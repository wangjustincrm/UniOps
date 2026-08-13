"""Intent products: planned SKUs that have no ERP material code yet.

They live in the forecast under an `INTENT-xxxxxxxx` placeholder code stored
in the ordinary `mrp_demand_series.material_code`, so the whole grid stack
(paste, autosave, change log, KG/t toggle) needs no special case. This table
only carries the human name and the binding lifecycle.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MrpIntentProduct(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_intent_products"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    bound_material_code: Mapped[str | None] = mapped_column(String(50))
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bound_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
