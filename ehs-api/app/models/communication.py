"""Email sent and received against a Safety record.

Phase 1 writes outbound only. The three columns that inbound handling needs —
`message_id`, `in_reply_to`, `is_auto_reply` — are created now so that adding
the inbound channel later is a service change with no migration.

`is_auto_reply` exists because the risk with filing replies against a legal
record is not missing one, it is admitting one that should never have been
there. An out-of-office bounce landing in an incident's audit trail is worse
than a reply arriving late, so anything matching the auto-response signatures
is stored but kept out of the record's main history.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Communication(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_communications"
    __table_args__ = (
        # Idempotency for inbound: a redelivered message must not double-file.
        UniqueConstraint("message_id", name="uq_ehs_comm_message_id"),
    )

    # Polymorphic attachment to any Safety record. Nullable doc_id: an inbound
    # message that could not be matched is still kept, and queued for someone
    # to attach by hand rather than dropped.
    doc_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    direction: Mapped[str] = mapped_column(String(3), nullable=False)  # out | in
    from_addr: Mapped[str] = mapped_column(String(320), nullable=False)
    to_addrs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    cc_addrs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    is_auto_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    attachments: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    template_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
