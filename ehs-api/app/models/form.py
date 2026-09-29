"""Form engine: templates, versions, submissions, answers.

The pairing that matters is on Submission: `schema_snapshot` keeps a filled-in
form readable after the template moves on, and the Answer rows keep it
queryable. Neither substitutes for the other.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class FormTemplate(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_form_templates"

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False,
                                          default="inspection", server_default="inspection")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                            default=True, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class FormVersion(UUIDPrimaryKey, TimestampMixin, Base):
    """A published version is immutable. Editing produces the next one.

    This is HSE's "template modification history" and, more importantly, what
    stops a form changing under someone who is halfway through filling it in.
    """

    __tablename__ = "ehs_form_versions"
    __table_args__ = (UniqueConstraint("template_id", "version_no",
                                       name="uq_ehs_form_version_no"),)

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_templates.id", ondelete="CASCADE"),
        nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    schema: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                          server_default="[]")
    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                        default="draft", server_default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class FormSubmission(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_form_submissions"

    submission_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_templates.id", ondelete="RESTRICT"),
        nullable=False, index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_versions.id", ondelete="RESTRICT"),
        nullable=False)
    schema_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)

    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                          nullable=True, index=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    shift_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                        default="draft", server_default="draft", index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ★ What this form is for. The link used to run one way only — a meeting
    # knew its minutes and the minutes knew nothing — so an unfilled set of
    # minutes was indistinguishable from a fresh blank form, which is what
    # "returns a new form not the same form back" was describing.
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(50), nullable=True)

    failed_count: Mapped[int] = mapped_column(Integer, nullable=False,
                                              default=0, server_default="0")


class FormAnswer(UUIDPrimaryKey, Base):
    """One answer. Typed columns because reports query these, not the snapshot."""

    __tablename__ = "ehs_form_answers"
    __table_args__ = (UniqueConstraint("submission_id", "question_key",
                                       name="uq_ehs_form_answer_key"),)

    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_submissions.id", ondelete="CASCADE"),
        nullable=False, index=True)
    question_key: Mapped[str] = mapped_column(String(80), nullable=False)
    # Snapshot of the wording, so an answer reads correctly even on its own.
    question_label: Mapped[str] = mapped_column(String(500), nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False)

    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_number: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    value_bool: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                             server_default="[]")
    value_labels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                                server_default="[]")

    is_failure: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                             default=False, server_default="false")
    action_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")


class FormAttachment(UUIDPrimaryKey, Base):
    """A photograph on a submission, optionally against one question."""

    __tablename__ = "ehs_form_attachments"

    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_submissions.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # A checklist can ask for photos at several points, so an attachment belongs
    # to a question rather than only to the form.
    question_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    storage_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")
