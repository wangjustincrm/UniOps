"""VmsConfig ORM model — singleton row holding VMS-local configuration.

Per VMS PRD §5.3. Mirrors the singleton pattern used by `company_config` in
epms-api and `budget_settings` in budget-api. Fields stored as JSONB to keep
schema additions Admin-editable without migrations.

Notable fields:
  - quality_manager_user_ids: VMS-local Quality Manager roster (UUIDs from
    public.users). Resolves the GMP-zone second-step approver per PRD §6.2.1.
  - notification_contacts: { training_email, ppe_email } per PRD §2.1.2.1.
  - health_questions: questionnaire template per PRD §2.2.2 VMS-CI-010.
  - badge_templates: HTML/CSS strings, keyed by template name.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VmsConfig(Base):
    __tablename__ = "vms_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    # ── VMS-local Quality Manager roster (PRD §3 / §6.2.1) ──────────────────
    # List of UniOps user UUIDs designated as Quality Manager candidates.
    quality_manager_user_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # ── Notification contacts (PRD §2.1.2.1) ────────────────────────────────
    # { "training_email": "...", "ppe_email": "..." }
    notification_contacts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ── Health declaration questionnaire template (PRD §2.2.2) ──────────────
    health_questions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ── Badge templates (HTML/CSS strings, keyed by template name) ──────────
    badge_templates: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ── Structured badge configuration (replaces badge_templates UI) ────────
    # Single global config object; shape validated by schemas.badge_config.
    # Empty dict → renderer falls back to DEFAULT_BADGE_CONFIG.
    badge_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ── VMS-local SMTP overrides ────────────────────────────────────────────
    # When non-empty, vms-api uses these creds for outbound email instead of
    # the shared `company_config.smtp_*` row. Lets ops point VMS at a
    # different mailbox without touching EPMS config.
    # Shape: { host, port, user, password, use_tls, from_email }
    smtp_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ── Audit ───────────────────────────────────────────────────────────────
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
