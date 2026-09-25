"""The policy and document library, and who has read what.

Two obligations meet here. OHSA s.25(2)(j) requires the health and safety
policy to be reviewed at least annually — that review is a statutory deadline
like any other, so it goes into `ehs_statutory_deadlines`. And a policy nobody
has read is not a control, which is what Read & Sign is for: the value of the
feature is the list of people who have *not* signed, so acknowledgement is a
row per person per version rather than a flag on the document.

Versioning is by supersession rather than by an edit history: publishing v3
retires v2, both rows stay, and an acknowledgement remains attached to the
version that was actually read. Someone who signed v2 shows as outstanding on
v3 — which is the correct answer, and the reason the version is on the
acknowledgement row.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class SafetyDocument(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_documents"

    doc_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    # policy | procedure | sds | guideline | form | other
    category: Mapped[str] = mapped_column(String(20), nullable=False,
                                          default="policy", server_default="policy", index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False,
                                         default="1.0", server_default="1.0")
    # The document itself, in file-api. A policy with no file is a draft.
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)

    effective_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Null means it does not need reviewing on a cycle — a form template does
    # not, a policy does.
    review_due_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    review_months: Mapped[int | None] = mapped_column(nullable=True)

    # draft | published | retired
    status: Mapped[str] = mapped_column(String(10), nullable=False,
                                        default="draft", server_default="draft", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The row this one replaced, so a retired version can be traced forward.
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_documents.id", ondelete="SET NULL"), nullable=True)

    requires_ack: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                               default=False, server_default="false")
    # Empty list = everybody. A list of role codes narrows it — the forklift
    # procedure does not need a signature from the accounts office.
    # Named individuals, in addition to whatever roles are listed. A procedure
    # that concerns four people on one line corresponds to no role, and sending
    # it to the nearest one teaches thirty others to ignore the next one.
    ack_user_ids: Mapped[list] = mapped_column(JSONB, nullable=False,
                                               default=list, server_default="[]")
    ack_roles: Mapped[list] = mapped_column(JSONB, nullable=False,
                                            default=list, server_default="[]")

    # Printed on the shelf label so a phone camera reaches the current version
    # without anybody navigating to it.
    qr_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DocumentAcknowledgement(UUIDPrimaryKey, Base):
    """One person confirming they have read one version."""

    __tablename__ = "ehs_document_acks"
    __table_args__ = (
        UniqueConstraint("document_id", "user_id", "version", name="uq_ehs_document_ack"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_documents.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # Snapshot of what was signed, not a join to what the document says now.
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_role: Mapped[str | None] = mapped_column(String(150), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")
