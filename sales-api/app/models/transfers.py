"""Stock transfers and their receipts (design §3.2; migration sales12_transfers;
FR-OB-80 … 84). The transfer's status is the TRANSFER state machine (9.1, with
the On Hold of a second, different 944)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.status import TRANSFER, Transfer as T
from app.models.config import one_of
from app.platform.entities import Audited, CreationAudited, Versioned

LINE_STATUSES = ("Open", "Partially Received", "Received", "Discrepancy")


class Transfer(Audited, Versioned, Base):
    __tablename__ = "sales_transfers"
    __table_args__ = (
        UniqueConstraint("transfer_number", name="uq_sales_transfers_number"),
        one_of("status", tuple(s.value for s in T), "ck_sales_transfers_status"),
    )

    transfer_number: Mapped[str] = mapped_column(String(10), nullable=False)  # 5 digits: 00104
    from_warehouse_id: Mapped[int] = mapped_column(ForeignKey("sales_warehouses.id"), nullable=False)
    to_warehouse_id: Mapped[int] = mapped_column(ForeignKey("sales_warehouses.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    status_before_hold: Mapped[str | None] = mapped_column(String(30), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("sales_documents.id"), nullable=True)  # the live 943
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


TRANSFER.bind(Transfer)


class TransferLine(Base):
    __tablename__ = "sales_transfer_lines"
    __table_args__ = (
        UniqueConstraint("transfer_id", "line_no", name="uq_sales_transfer_lines_line"),
        one_of("line_status", LINE_STATUSES, "ck_sales_transfer_lines_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("sales_transfers.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_code: Mapped[str] = mapped_column(String(50), ForeignKey("sales_items.item_code"), nullable=False)
    qty_ea: Mapped[int] = mapped_column(Integer, nullable=False)
    received_ea: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    line_status: Mapped[str] = mapped_column(String(20), nullable=False, default="Open", server_default="Open")


class Receipt(CreationAudited, Base):
    __tablename__ = "sales_receipts"
    __table_args__ = (UniqueConstraint("document_id", name="uq_sales_receipts_document"),)

    document_id: Mapped[int] = mapped_column(ForeignKey("sales_documents.id"), nullable=False)
    transfer_id: Mapped[int | None] = mapped_column(ForeignKey("sales_transfers.id"), nullable=True)
    transfer_key_received: Mapped[str | None] = mapped_column(String(20), nullable=True)
    receipt_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_ea: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    discrepancies: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)


class ReceiptRow(Base):
    __tablename__ = "sales_receipt_rows"
    __table_args__ = (UniqueConstraint("receipt_id", "position", name="uq_sales_receipt_rows_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("sales_receipts.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)   # the W07's place in the file (1-based)
    lx_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    item_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    upc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lot: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    qty_ea: Mapped[int] = mapped_column(Integer, nullable=False)
    transfer_line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)



# The facts a transfer list shows and sums (user 2026-11-13: as the orders' list): read with the row.
from sqlalchemy import func, literal, select  # noqa: E402
from sqlalchemy.dialects.postgresql import aggregate_order_by  # noqa: E402
from sqlalchemy.orm import column_property  # noqa: E402

from app.models.documents import Document, DocumentLink  # noqa: E402

Transfer.sent_ea = column_property(select(func.coalesce(func.sum(TransferLine.qty_ea), 0)).where(
    TransferLine.transfer_id == Transfer.id).correlate_except(TransferLine).scalar_subquery())
Transfer.received_ea = column_property(select(func.coalesce(func.sum(TransferLine.received_ea), 0)).where(
    TransferLine.transfer_id == Transfer.id).correlate_except(TransferLine).scalar_subquery())
Transfer.items = column_property(select(func.string_agg(TransferLine.item_code, aggregate_order_by(literal(", "), TransferLine.line_no))).where(
    TransferLine.transfer_id == Transfer.id).correlate_except(TransferLine).scalar_subquery())
Transfer.receipt_date = column_property(select(func.max(Receipt.receipt_date)).where(
    Receipt.transfer_id == Transfer.id, Receipt.applied.is_(True)).correlate_except(Receipt).scalar_subquery())
Transfer.attachments = column_property(select(func.count(DocumentLink.id)).join(Document, Document.id == DocumentLink.document_id).where(
    DocumentLink.entity_type == "transfer", DocumentLink.entity_id == Transfer.id, Document.kind == "ATTACHMENT")
    .correlate_except(DocumentLink, Document).scalar_subquery())
