"""Visit ORM model — a single appointment / on-site session.

Per VMS PRD §5.2. The approval-related fields (approval_step_idx,
submitted_at, quality_approver_id) are written by approval-api via its
thin Visit mirror (§6.2.1). vms-api owns the full lifecycle otherwise.
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class VisitStatus(str, enum.Enum):
    pending_approval = "pending_approval"
    confirmed = "confirmed"
    checked_in = "checked_in"
    checked_out = "checked_out"
    cancelled = "cancelled"
    no_show = "no_show"


class AccessArea(str, enum.Enum):
    office = "office"
    warehouse = "warehouse"
    production_non_gmp = "production_non_gmp"
    production_gmp = "production_gmp"
    laboratory = "laboratory"
    all = "all"


class VisitPurpose(str, enum.Enum):
    meeting = "meeting"
    maintenance = "maintenance"
    tour = "tour"
    audit = "audit"
    interview = "interview"
    delivery = "delivery"
    other = "other"


class HealthDeclStatus(str, enum.Enum):
    not_required = "not_required"
    passed = "passed"
    failed = "failed"
    restricted = "restricted"


class Visit(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vms_visits"

    # ── Identity ────────────────────────────────────────────────────────────
    visitor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vms_visitors.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Companion visitors for the same appointment. Empty list = single-visitor
    # visit (the historical case). JSONB list of Visitor UUIDs — no FK because
    # PG doesn't enforce per-element FK on JSON.
    additional_visitor_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    host_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ── Scheduling ──────────────────────────────────────────────────────────
    visit_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    planned_arrival:   Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_arrival:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_departure:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Purpose / area ──────────────────────────────────────────────────────
    visit_purpose: Mapped[VisitPurpose] = mapped_column(
        SAEnum(VisitPurpose, name="vms_visit_purpose"), nullable=False,
    )
    access_area: Mapped[AccessArea] = mapped_column(
        SAEnum(AccessArea, name="vms_access_area"), nullable=False,
    )

    # ── State ───────────────────────────────────────────────────────────────
    status: Mapped[VisitStatus] = mapped_column(
        SAEnum(VisitStatus, name="vms_visit_status"),
        nullable=False,
        default=VisitStatus.confirmed,
        index=True,
    )

    # ── Compliance ──────────────────────────────────────────────────────────
    health_decl_status: Mapped[HealthDeclStatus | None] = mapped_column(
        SAEnum(HealthDeclStatus, name="vms_health_decl_status"), nullable=True,
    )
    safety_training_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    badge_returned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ppe_issued: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Free-form attendant fields ──────────────────────────────────────────
    accompanying_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vehicle_plate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Approval workflow fields (consumed by approval-api, see PRD §6.2.1) ─
    # approval-api's thin Visit mirror reads/writes these via the shared DB row.
    # `approval_status` holds the engine-state vocabulary (draft/submitted/
    # in_review/approved/returned/rejected/cancelled) — separate from the
    # user-facing `status: VisitStatus`. See S2_ARCHITECTURE_REVIEW.md F1.
    approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    approval_step_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Populated by vms-api at submit time: "VMS Visit — {first} {last} ({co})".
    # approval-api reads it via `meta["number_attr"]` so Portal task inbox rows
    # have meaningful labels without a join. See S2_ARCHITECTURE_REVIEW.md F3.
    visit_title: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    # Per-instance approver assignment for the VMS-local Quality Manager step.
    # vms-api populates this from vms_config.quality_manager_user_ids when
    # submitting a GMP/lab visit; approval-api uses it as assignee_id directly.
    quality_approver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Timestamp the Host was emailed about the resolved approval result.
    # Set once on first read after the engine reaches a terminal state — the
    # read-side `sync_status_from_approval` helper uses this as an idempotency
    # flag to avoid double-sends. Per S2_ARCHITECTURE_REVIEW.md F9.
    host_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Host-specified PPE request (clothing size, footwear choice, shoe size,
    # free-form notes). None when the host didn't tick the "PPE needed" box.
    # Schema lives in app.schemas.visit.PpeRequest.
    ppe_requested: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Timestamp the Janitor was emailed about this visit's PPE request.
    # Set once on first send (immediate-confirm visit or post-approval read).
    ppe_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Scheduled-job idempotency flags (PRD VMS-PR-012 / VMS-CO-010 / -011) ─
    # One-shot timestamp flags set by the background scheduler (app.services.
    # scheduler) so a reminder / escalation fires at most once per visit even
    # though the tick re-runs every few minutes. Same pattern as
    # host_notified_at / ppe_notified_at above. No-show (VMS-PR-019) needs no
    # flag — it's a status transition (confirmed → no_show), naturally one-shot.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # day-before reminder to Host (VMS-PR-012)
    overdue_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # 1h-overdue reminder to Host (VMS-CO-010)
    overdue_escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # 4h-overdue escalation to dept manager (VMS-CO-011)
