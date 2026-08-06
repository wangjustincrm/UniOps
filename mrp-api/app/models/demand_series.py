"""Continuous demand series + append-only change log (Task 1 of the
Continuous Sales Forecast redesign, plan doc
2026-08-06-continuous-sales-forecast). `MrpDemandSeries` replaces the
version-scoped `ForecastLine` grid as the single source of truth going
forward: `ForecastVersion`/`ForecastLine` become frozen "outlook snapshot"
tables in a later task (via the new `source_anchor_month` column added to
`ForecastVersion` in this same migration).
"""
import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MrpDemandSeries(Base, UUIDPrimaryKey, TimestampMixin):
    """The single living demand table: one row per (finished good, absolute
    month). Unbounded in time, no version_id — the source of truth that
    outlook snapshots are frozen from (design §4.1). Sparse: a row exists
    only for a non-zero cell."""

    __tablename__ = "mrp_demand_series"
    __table_args__ = (
        UniqueConstraint("material_code", "month", name="uq_mrp_demand_series_material_month"),
    )

    material_code: Mapped[str] = mapped_column(String(50), index=True)
    month: Mapped[str] = mapped_column(CHAR(7), index=True)  # 'YYYY-MM'
    qty: Mapped[object] = mapped_column(Numeric(18, 3), default=0)
    uom: Mapped[str] = mapped_column(String(10), default="KG", server_default="KG")


class MrpForecastChangeLog(Base, UUIDPrimaryKey):
    """Append-only audit of every series cell edit (design §4.2): who/when/
    month/old->new. No TimestampMixin — this is immutable, it has its own
    changed_at and never updates."""

    __tablename__ = "mrp_forecast_change_log"

    material_code: Mapped[str] = mapped_column(String(50), index=True)
    month: Mapped[str] = mapped_column(CHAR(7), index=True)
    old_qty: Mapped[object | None] = mapped_column(Numeric(18, 3))
    new_qty: Mapped[object | None] = mapped_column(Numeric(18, 3))
    source: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
