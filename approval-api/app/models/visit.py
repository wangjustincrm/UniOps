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
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class Visit(UUIDPrimaryKey, Base):
    """Thin mirror — only fields the engine needs."""
    __tablename__ = "vms_visits"

    # ── Owned by vms-api (engine reads only) ────────────────────────────────
    visitor_id:  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    host_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by:  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    access_area: Mapped[str]       = mapped_column(String(40), nullable=False)
    visit_title: Mapped[str]       = mapped_column(String(255), nullable=False, default="")
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
        """Engine line ~345 does `doc.title` directly when rendering task title."""
        return self.visit_title or f"VMS Visit {str(self.id)[:8]}"
