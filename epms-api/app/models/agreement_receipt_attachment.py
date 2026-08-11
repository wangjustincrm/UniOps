"""ORM model for agreement receipt attachments."""
import uuid

from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class AgreementReceiptAttachment(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "agreement_receipt_attachments"

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agreement_receipts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/octet-stream")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    storage_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


# TODO(Task 3): delete this alias once app/api/v1/agreement_slip_attachments.py
# moves onto AgreementReceiptAttachment directly. See agreement_receipt.py for
# why this exists — same reasoning, same layer boundary.
AgreementSlipAttachment = AgreementReceiptAttachment
