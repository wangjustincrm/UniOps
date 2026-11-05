"""Read-write mirror of vms_visits for the approval engine.

vms-api owns the full Visit model (with the VisitStatus enum). This mirror
exposes ONLY the columns the engine reads/writes — same DB row, narrower view.

Per S2_ARCHITECTURE_REVIEW.md F1 (status_attr indirection):
  - engine reads/writes `approval_status` (engine vocabulary: draft / submitted
    / in_review / approved / returned / rejected / cancelled), NOT `status`.
  - `status` (the VisitStatus enum, vms-api owns) is touched only by the
    post-approve callback (`_post_approve_vms_visit`), and even then via raw
    SQL because the enum type doesn't round-trip through this mirror.

Per S2_ARCHITECTURE_REVIEW.md F3 (real title column):
  - `visit_title` is populated by vms-api at submit time so engine can render
    Portal task titles without doing a cross-table join.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class VmsZone(UUIDPrimaryKey, Base):
    """Read-only mirror of vms_zones (vms-api owns it) — only the zone's
    display name, for approval task titles."""
    __tablename__ = "vms_zones"

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)


class Visit(UUIDPrimaryKey, Base):
    """Thin mirror — only fields the engine needs."""
    __tablename__ = "vms_visits"

    # ── Owned by vms-api (engine reads only) ────────────────────────────────
    visitor_id:  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    host_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by:  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    access_area: Mapped[str]       = mapped_column(String(40), nullable=False)
    visit_date:  Mapped[date | None] = mapped_column(Date, nullable=True)
    visit_title: Mapped[str]       = mapped_column(String(255), nullable=False, default="")
    # The zone's managers at submit time — who gets the Zone Manager step.
    zone_approver_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Superseded by zone_approver_ids; no longer read or written.
    quality_approver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # ── Owned by approval-api (engine reads AND writes) ─────────────────────
    approval_status:   Mapped[str | None] = mapped_column(String(20), nullable=True)
    approval_step_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_at:      Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Synthetic attributes the engine reads via _DOC_META["…_attr"] keys ──
    @property
    def title(self) -> str:
        """The part of the task title after the document number.

        The number is already `visit_title` (who is visiting), so the title
        adds where and when — repeating `visit_title` here printed the visitor
        twice in every approval task.
        """
        area = (self.zone_name or _AREA_LABELS.get(self.access_area)
                or (self.access_area or "").replace("_", " "))
        when = self.visit_date.isoformat() if self.visit_date else ""
        return ", ".join(p for p in (area, when) if p) or f"Visit {str(self.id)[:8]}"


Visit.zone_name = column_property(
    select(VmsZone.name).where(VmsZone.code == Visit.access_area)
    .correlate_except(VmsZone).scalar_subquery()
)


# Fallback when the zone row is missing — the six original zones' names.
_AREA_LABELS = {
    "office": "Office",
    "warehouse": "Warehouse",
    "production_non_gmp": "Production (Non-GMP)",
    "production_gmp": "Production (GMP)",
    "laboratory": "Laboratory",
    "all": "Entire Plant",
}
