"""Safety equipment, and the inspections it is due.

Extinguishers, AEDs, eyewash stations, safety showers, first-aid cabinets, gas
detectors, ladders, and the fall-arrest harnesses and lanyards HSE added. Not
production machinery — that is `ehs_equipment`, maintained by a different
person for a different reason.

The inspection cycle is deliberately *not* the form scheduler. That one asks
"who does this month's warehouse inspection"; this one asks "when was this
particular extinguisher last checked", and the answer has to be per asset or
the register is useless. Both open a task and both can be answered with a
form, but a schedule that rotates departments cannot express "asset 47 is
overdue".
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class SafetyAsset(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_safety_assets"

    asset_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_type_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Snapshot of the vocabulary label, per the module-wide rule.
    asset_type_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    serial_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(150), nullable=True)
    model: Mapped[str | None] = mapped_column(String(150), nullable=True)

    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                          nullable=True, index=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # monthly | quarterly | semiannual | annual — null means it is registered
    # but not on a cycle.
    inspection_frequency: Mapped[str | None] = mapped_column(String(15), nullable=True)
    # The checklist to fill in. Optional: a monthly extinguisher check can be a
    # pass/fail with a note, and demanding a template first would stop the
    # register being usable on day one.
    inspection_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_templates.id", ondelete="SET NULL"),
        nullable=True)
    assigned_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    next_due_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    last_inspected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # For equipment with a service life — a harness is condemned at ten years
    # whatever its condition.
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    # in_service | out_of_service | retired. Out of service is the state that
    # matters: an extinguisher that failed its check must stop counting as
    # coverage for its area.
    status: Mapped[str] = mapped_column(String(15), nullable=False,
                                        default="in_service", server_default="in_service",
                                        index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)


class AssetInspection(UUIDPrimaryKey, TimestampMixin, Base):
    """One check of one asset. An overdue check is a row, not an absence."""

    __tablename__ = "ehs_asset_inspections"
    # The generator runs on every sweep; this is what makes a restart mid-run
    # safe rather than producing a second check for the same due date.
    __table_args__ = (
        UniqueConstraint("asset_id", "due_on", name="uq_ehs_asset_inspection_due"),
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_safety_assets.id", ondelete="CASCADE"),
        nullable=False, index=True)
    due_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    performed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    performed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    performed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # due | passed | failed | missed
    result: Mapped[str] = mapped_column(String(10), nullable=False,
                                        default="due", server_default="due", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_submissions.id", ondelete="SET NULL"),
        nullable=True)
    # A failed check raises one, the same way a failed inspection question does.
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_actions.id", ondelete="SET NULL"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Kept so a re-run of the generator never produces a second row for the
    # same cycle even if the asset's frequency changed in between.
    cycle_index: Mapped[int] = mapped_column(Integer, nullable=False,
                                             default=0, server_default="0")
