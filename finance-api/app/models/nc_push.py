"""Audit trail of AP pushes to NC (XChange), one row per attempt.

Kept apart from ap_invoices so a re-push never loses what NC answered the
previous time — the request XML and NC's raw response are both stored.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class NcPushLog(UUIDPrimaryKey, Base):
    __tablename__ = "nc_push_log"

    ap_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ap_invoices.id", ondelete="CASCADE"),
        nullable=False, index=True)
    pushed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pushed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    external_id: Mapped[str] = mapped_column(String(60), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nc_bill_pk: Mapped[str | None] = mapped_column(String(25), nullable=True)
    nc_bill_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_xml: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_xml: Mapped[str | None] = mapped_column(Text, nullable=True)
