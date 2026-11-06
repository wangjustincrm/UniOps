"""User-maintained configuration (Settings page)."""
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Warehouse classes (design §4.6). Only `wms` takes part in WMS↔NC
# comparison. `transient` (瞬时临时仓) is neither compared nor listed, but its
# quantity still counts in the IC total — IA has no warehouse dimension, so
# leaving it out would invent an IA−IC difference.
WAREHOUSE_CLASSES = ("wms", "external", "production", "transient", "other")


class InvParam(Base):
    """Key-value settings: sync interval, NC time zone, tolerances."""
    __tablename__ = "inv_params"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvWarehouseClass(Base):
    """NC warehouse code → class. Keyed by the NC code (SC11 …), which is what
    people say and what survives an NC re-import, not by pk_stordoc. A warehouse
    with no row here is *unclassified*: never compared with WMS, and flagged."""
    __tablename__ = "inv_warehouse_class"

    nc_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    warehouse_class: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvNoBatchMaterial(Base):
    """Materials NC stopped tracking by batch (design D11: CF00AF / CF00WT
    from 2026-01-01). Compared at material level only, and their WMS↔NC
    difference after `since_date` is an explained category, not a finding."""
    __tablename__ = "inv_no_batch_material"

    material_code: Mapped[str] = mapped_column(String(60), primary_key=True)
    since_date: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
