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
    # Write-time denormalization of the editor's display name (mrp06 follow-up
    # to the Continuous Sales Forecast feature). The access JWT carries only
    # {sub, role, type} — no name — and identity-api has no batch/list-users
    # endpoint to resolve `changed_by` UUIDs after the fact, only
    # GET /identity/v1/auth/me for the CURRENT caller. Since the editor IS the
    # current user at write time, `app/api/v1/series.py`'s PUT /series/cells
    # resolves this once via app/services/identity_client.py and stores it
    # here — so the change-history popover can render a name without ever
    # needing a live UUID -> name lookup. Nullable: identity-api being down at
    # write time (or a pre-mrp06 row) degrades to no name shown, never a
    # raised error and never the raw UUID.
    changed_by_name: Mapped[str | None] = mapped_column(String(200))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
