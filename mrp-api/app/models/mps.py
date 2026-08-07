"""Master Production Schedule run/line (Phase 1B Task 2, design §6.4).

A run (`MrpMpsRun`) is one planning pass over a forecast version, producing
one line (`MrpMpsLine`) per material x demand-month x plan-month. Lines can
differ from the demand month when the algorithm pre-builds for shelf-life
reasons (`is_prebuild`/`prebuild_reason`) or hits a capacity constraint
(`capacity_gap`). Planners can lock (`locked_by_planner`) or hand-edit
(`manual_adjusted`) individual lines before a run is confirmed/released.

DDL lives in `alembic/versions/mrp04_capacity_mps_demand.py` alongside
`mrp_capacity_rules` (Task 1) and `mrp_demands` (this task's other model,
`app/models/demand.py`) — don't rename that migration.
"""
import uuid

from sqlalchemy import Boolean, CHAR, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MrpMpsRun(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_mps_runs"
    run_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    forecast_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    horizon_start_month: Mapped[str] = mapped_column(CHAR(7))
    horizon_months: Mapped[int] = mapped_column(default=18)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")  # draft|confirmed|released
    safety_margin_fraction: Mapped[object] = mapped_column(Numeric(6, 4), default=0)  # shelf-life pre-build safety, e.g. 0.3333
    generated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stats: Mapped[dict | None] = mapped_column(JSONB)


class MrpMpsLine(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_mps_lines"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mrp_mps_runs.id", ondelete="CASCADE"), index=True)
    material_code: Mapped[str] = mapped_column(String(50), index=True)
    demand_month: Mapped[str] = mapped_column(CHAR(7))
    plan_month: Mapped[str] = mapped_column(CHAR(7))
    qty: Mapped[object] = mapped_column(Numeric(18, 3))
    is_prebuild: Mapped[bool] = mapped_column(Boolean, default=False)
    prebuild_reason: Mapped[str | None] = mapped_column(Text)
    shelf_life_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    capacity_gap: Mapped[bool] = mapped_column(Boolean, default=False)  # couldn't place -> exception
    locked_by_planner: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    # Demand context snapshot (mrp07, Production Plan Matrix final-review fix):
    # the gross forecast for demand_month and the rolled-forward opening
    # stock entering it, as they stood at generate/recalculate time -- NOT
    # recomputed from live inventory on every read (a released run must stay
    # a frozen point-in-time snapshot). NULL for pre-mrp07 lines; the read
    # side falls back to 0 (see app/api/v1/mps.py's _line_response).
    demand_forecast: Mapped[object | None] = mapped_column(Numeric(18, 3))
    opening_stock: Mapped[object | None] = mapped_column(Numeric(18, 3))
