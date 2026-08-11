"""ORM model for house-account pickup slips.

一张纸质凭证(柜台小票/送货单)的数字记录。它在 house_account 这条免收货链路上
扮演 GR 的角色 —— 但 ⚠️ **它不是领用人的数字签认**:小票由员工交给财务、财务
代录(设计 §0 决策 7),picked_by 是代录人据交接事实填的。任何 UI 文案都不得
写成"领用人已确认"。
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class AgreementPickupSlip(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "agreement_pickup_slips"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_agreements.id", ondelete="CASCADE"),
        nullable=False, index=True)

    # slip_date 与 total_amount 是基线匹配仅有的两个依据(设计 §4.1),都不可空。
    slip_date: Mapped[date] = mapped_column(Date, nullable=False)

    # 凭证上的参考号 —— **什么都行**:小票号、交易号、送货单号。
    # Princess Auto 的情况下由 OCR 抽出的 TILL + TRANS 拼成 "1-510076",
    # 但模型不关心它怎么来的,只当它是个不透明字符串。抽不到就是 NULL,
    # 基线匹配照常工作。
    slip_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default="0")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # 领用人 = 交单人。见类文档:这不是数字签认。
    picked_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    missing_slip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ap_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    ap_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # pending_ap_review | open | reconciled | voided | rejected
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="open", index=True)

    # 认领它的发票。一张小票只属于一张发票;反过来一张发票可覆盖多张小票
    # (invoices.slip_ids 是数组),所以这里**不加唯一索引**。
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    __table_args__ = (
        sa.Index("uq_agr_slip_ref_per_agreement", "agreement_id", "slip_ref",
                 unique=True, postgresql_where=sa.text("slip_ref IS NOT NULL")),
    )
