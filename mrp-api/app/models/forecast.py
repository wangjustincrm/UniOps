"""Sales forecast versions + monthly demand grid lines (Phase 1A Task 1).

`ForecastVersion` is one working "sheet" of a rolling sales forecast:
`horizon_start_month` + `horizon_months` (default 18) implicitly define the
set of YYYY-MM columns the grid shows — the API always regenerates that
month list server-side from these two fields (see
app/api/v1/forecast.py::_generate_months); it is never persisted as its own
list and never trusted from a client request body.

Several versions may hold status='confirmed' at once (Continuous Sales
Forecast redesign, Task 4, design §4.3): confirming a version — whether via
the /confirm endpoint or `app/services/demand_series.py::freeze_outlook`'s
"Generate Outlook" snapshot — no longer supersedes any other confirmed
version; 'superseded' is a legacy status a version could carry from before
this change, never assigned by current code. 'draft' versions are freely
writable via the grid cell API; 'confirmed'/'superseded' versions are
immutable (writes 409).

`ForecastLine.freeze_flag` marks a single (material_code, month) cell as
locked against the bulk-upsert endpoint — used to protect a manually
adjusted number from being clobbered by a re-import (Task 2) or another
planner's edit; skip-and-report, never silently overwritten.
"""
import uuid
from datetime import datetime

from sqlalchemy import CHAR, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class ForecastVersion(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_forecast_versions"

    version_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")  # draft|confirmed|superseded
    horizon_start_month: Mapped[str] = mapped_column(CHAR(7))  # 'YYYY-MM'
    horizon_months: Mapped[int] = mapped_column(Integer, default=18, server_default="18")
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_anchor_month: Mapped[str | None] = mapped_column(CHAR(7))  # window anchor a snapshot was frozen from; NULL for legacy hand-built versions


class ForecastLine(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_forecast_lines"
    __table_args__ = (
        UniqueConstraint("version_id", "material_code", "month", name="uq_mrp_forecast_lines_version_material_month"),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mrp_forecast_versions.id", ondelete="CASCADE"), index=True,
    )
    material_code: Mapped[str] = mapped_column(String(50), index=True)
    month: Mapped[str] = mapped_column(CHAR(7))  # 'YYYY-MM'
    qty: Mapped[object] = mapped_column(Numeric(18, 3), default=0, server_default="0")
    uom: Mapped[str] = mapped_column(String(10), default="KG", server_default="KG")
    freeze_flag: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
