"""Purchase suggestion run/line (Phase 1C).

A run is one calculation over the production plan currently in force; a
line is one material's suggested order for one need week. Purchasing acts
on these numbers, so runs are kept rather than overwritten -- "what did it
say last week" is a question that gets asked, and a table that only ever
holds the latest answer cannot answer it.

DDL lives in `alembic/versions/mrp13_purchase_suggestions.py`.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MrpPurchaseRun(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_purchase_runs"

    run_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    # Which production plan produced the demand this was computed from.
    source_plan_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), index=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Snapshotted, like the plan's own calendar settings: a suggestion must
    # keep meaning what it meant when it was produced, even after somebody
    # edits the rates.
    raw_material_loss_rate: Mapped[object] = mapped_column(
        Numeric(6, 4), default=0, server_default="0")
    packaging_loss_rate: Mapped[object] = mapped_column(
        Numeric(6, 4), default=0, server_default="0")
    stats: Mapped[dict | None] = mapped_column(JSONB)


class MrpPurchaseLine(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_purchase_lines"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mrp_purchase_runs.id", ondelete="CASCADE"),
        index=True)
    material_code: Mapped[str] = mapped_column(String(50), index=True)
    # The week the material is needed, and the date to place the order by
    # (need week minus the supplier's lead time).
    need_week: Mapped[date] = mapped_column(Date, index=True)
    order_date: Mapped[date] = mapped_column(Date)
    gross_qty: Mapped[object] = mapped_column(Numeric(18, 3))
    available_qty: Mapped[object] = mapped_column(Numeric(18, 3))
    net_qty: Mapped[object] = mapped_column(Numeric(18, 3))
    suggested_qty: Mapped[object] = mapped_column(Numeric(18, 3))
    raised_to_moq: Mapped[object] = mapped_column(
        Numeric(18, 3), default=0, server_default="0")
    partner_code: Mapped[str | None] = mapped_column(String(50))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    # Each unknown carries its own flag rather than being folded into one
    # "check this" marker: they need different fixes (add a supplier, fill a
    # lead time, expedite) and a planner must be able to tell them apart.
    supplier_missing: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false")
    lead_time_missing: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false")
    order_date_passed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false")
    # pending | ordered | ignored — the hand-off point for the next round's
    # purchase requisitions, so wiring PRs needs no data migration.
    status: Mapped[str] = mapped_column(String(20), default="pending",
                                        server_default="pending")
