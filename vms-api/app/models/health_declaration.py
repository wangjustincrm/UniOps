"""HealthDeclaration ORM model — required for GMP/lab visits.

Per VMS PRD §5.2 / §2.2.2 VMS-CI-010. `questionnaire_data` stores the full
question + answer snapshot (not refs) so template changes don't retroactively
alter the historical record (PRD-AU-013 e-signature tamper-proofing).
"""
import uuid

from sqlalchemy import Enum as SAEnum, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.visit import HealthDeclStatus


class HealthDeclaration(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vms_health_declarations"
    # One declaration per (visit, visitor): every person on a multi-visitor
    # appointment files their own.
    __table_args__ = (
        UniqueConstraint("visit_id", "visitor_id", name="uq_health_decl_visit_visitor"),
    )

    visit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vms_visits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    visitor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vms_visitors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    questionnaire_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result: Mapped[HealthDeclStatus] = mapped_column(
        SAEnum(HealthDeclStatus, name="vms_health_decl_status", create_type=False),
        nullable=False,
    )
    # base64 PNG of signature pad image; populated when visitor signs.
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
