"""Documents and runs (design §3.2; migration sales03_documents_runs).

- `sales_documents`: the document store (FR-OB-05, D22). Original bytes are
  kept; nothing is deleted and content is never updated (§13.1:
  CreationAudited only). Two identities (FR-OB-71): the remote file
  (connection, name, size, mtime) of an inbound file, and the sha256 of its
  bytes. A file whose bytes are already stored, or a file that is not ours
  (FR-OB-75), is recorded as an identity row WITHOUT content — Duplicate or
  Skipped — so the next pass recognises it and downloads nothing (AC-OB-04);
  only those two may lack content.
- `sales_document_links`: a document's orders and transfers (P3 onward).
- `sales_runs`: one row per scheduled-job run (the platform's run record).
- `sales_log`: what happened, searchable by run, document, order, date
  (FR-OB-53). Order and transfer ids get their foreign keys with those tables.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db.base import Base
from app.domain.documents import DIRECTIONS, KINDS, LOG_LEVELS, ORIGINS, RUN_STATUSES, RUN_TRIGGERS, SYSTEMS
from app.domain.status import ACK, INBOUND_FILE, OUTBOUND_FILE, Ack, InboundFile, OutboundFile
from app.models.config import one_of
from app.platform.entities import CreationAudited
from app.platform.jobs import RunRecordMixin

# Content-less identity rows (see the module docstring).
NO_CONTENT = (InboundFile.DUPLICATE.value, InboundFile.SKIPPED.value)


class Document(CreationAudited, Base):
    __tablename__ = "sales_documents"
    __table_args__ = (
        one_of("direction", DIRECTIONS, "ck_sales_documents_direction"),
        one_of("origin", ORIGINS, "ck_sales_documents_origin"),
        one_of("system", SYSTEMS, "ck_sales_documents_system"),
        one_of("kind", KINDS, "ck_sales_documents_kind"),
        one_of("processing_status", tuple(s.value for s in InboundFile), "ck_sales_documents_processing_status"),
        one_of("file_status", tuple(s.value for s in OutboundFile), "ck_sales_documents_file_status"),
        one_of("ack_status", tuple(s.value for s in Ack), "ck_sales_documents_ack_status"),
        CheckConstraint(
            "content IS NOT NULL OR processing_status IN ('Duplicate', 'Skipped')",
            name="ck_sales_documents_content",
        ),
        # Identical bytes are stored once; identity rows carry no content.
        Index("uq_sales_documents_sha256", "sha256", unique=True, postgresql_where=text("content IS NOT NULL")),
        # An inbound remote file is read once (connection, name, size, mtime).
        Index(
            "uq_sales_documents_remote_file", "connection_id", "remote_name", "size", "remote_mtime",
            unique=True, postgresql_where=text("direction = 'IN'"),
        ),
        Index("ix_sales_documents_created_at", "created_at"),
    )

    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    system: Mapped[str | None] = mapped_column(String(20), nullable=True)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("sales_connections.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    remote_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remote_mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # X1: the original bytes. Deferred: lists never load them (has_content is SQL).
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    body_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # SQL NULL until parsed
    processing_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # inbound
    file_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # outbound
    ack_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    isa13: Mapped[str | None] = mapped_column(String(9), nullable=True)
    gs06: Mapped[str | None] = mapped_column(String(9), nullable=True)
    st02: Mapped[str | None] = mapped_column(String(9), nullable=True)
    partner_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    replaced_by_id: Mapped[int | None] = mapped_column(ForeignKey("sales_documents.id"), nullable=True)
    zoho_file_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    has_content: Mapped[bool] = column_property(content.column.is_not(None))


for machine in (INBOUND_FILE, OUTBOUND_FILE, ACK):
    machine.bind(Document)  # every status change is audited (§13.6)


class DocumentLink(Base):
    __tablename__ = "sales_document_links"
    __table_args__ = (UniqueConstraint("document_id", "entity_type", "entity_id", name="uq_sales_document_links"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("sales_documents.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)  # order | transfer
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Run(RunRecordMixin, Base):
    __tablename__ = "sales_runs"
    __table_args__ = (
        one_of("trigger", RUN_TRIGGERS, "ck_sales_runs_trigger"),
        one_of("status", RUN_STATUSES, "ck_sales_runs_status"),
    )


class LogEntry(Base):
    __tablename__ = "sales_log"
    __table_args__ = (
        one_of("level", LOG_LEVELS, "ck_sales_log_level"),
        Index("ix_sales_log_at", "at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("sales_runs.id"), nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    step: Mapped[str] = mapped_column(String(40), nullable=False)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("sales_connections.id"), nullable=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("sales_documents.id"), nullable=True, index=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    transfer_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
