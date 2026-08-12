"""ORM model for agreement receipts — typed evidence for house-account invoices.

一份挂账协议的收货/交单凭证的数字记录。它在 house_account 这条免收货链路上
扮演 GR 的角色 —— 但 ⚠️ **它不是领用人的数字签认**:凭证由员工交给财务、财务
代录(设计 §0 决策 7),received_by 是代录人据交接事实填的。任何 UI 文案都不得
写成"领用人已确认"。

receipt_type(counter_slip | delivery | service)是判别列:一份 house account
未必是柜台领用账户,它可能是按月送货或外包服务。三种类型共用同一组列,差异只
体现在 UI 文案 —— 把凭证写死成"小票"会让整个特性只能服务一个供应商(本分支
用户验收时指出的问题)。合法取值的校验放在 Task 2 的 Pydantic schema 层,本层
不加 CHECK 约束(与本表 status 列的既有做法一致)。
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class AgreementReceipt(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "agreement_receipts"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_agreements.id", ondelete="CASCADE"),
        nullable=False, index=True)

    # counter_slip | delivery | service —— 不传时落 counter_slip:这是存量语义
    # (柜台小票)本来就默认的行为。
    receipt_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="counter_slip", index=True)

    # receipt_date 与 total_amount 是基线匹配仅有的两个依据(设计 §4.1),都不可空。
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)

    # 凭证上的参考号 —— **什么都行**:小票号、交易号、送货单号。
    # Princess Auto 的情况下由 OCR 抽出的 TILL + TRANS 拼成 "1-510076",
    # 但模型不关心它怎么来的,只当它是个不透明字符串。抽不到就是 NULL,
    # 基线匹配照常工作。
    receipt_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # 小票抬头上印的商家名 —— **不是**协议的供应商(那个在 purchase_agreements
    # 上,一直都有)。存它的唯一理由是拿它跟协议的供应商比对:拿 A 家的小票报到
    # B 家的挂账户上是挂账户最常见的错归属,而只看协议的 vendor 永远看不出来。
    # 可空:OCR 抽不到(抬头模糊/被裁掉)是正常情况,手工录入也允许留空 ——
    # 没填不等于填错,派生的不一致判定对空值一律判"不冲突"
    # (见 schemas/agreement_receipt.py::is_vendor_mismatch)。
    # ⚠️ 测试库的表来自 Base.metadata.create_all 而不走 alembic,所以这一列
    # 必须与 ag06_receipt_vendor 逐字保持一致(与本文件下方 __table_args__ 的
    # 同步警告同一个理由)。
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default="0")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # 领用人 = 交单人。见类文档:这不是数字签认。
    received_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    missing_receipt_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ap_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    ap_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # pending_ap_review | open | reconciled | voided | rejected
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="open", index=True)

    # 认领它的发票。一份凭证只属于一张发票;反过来一张发票可覆盖多份凭证
    # (invoices.receipt_ids 是数组),所以这里**不加唯一索引**。
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    # ⚠️ Predicate kept byte-for-byte in sync with alembic ag04_agreement_receipts.
    # The test DB is built by Base.metadata.create_all and never runs the
    # migrations, so a predicate that lives only in the migration is a
    # predicate no test can see (this branch already shipped one such
    # invisible constraint once — that's why this file inherited the warning
    # instead of losing it in the rename).
    #
    # Retired rows are excluded from uniqueness on purpose: voiding a
    # mis-keyed receipt or having AP reject one used to burn its receipt_ref
    # inside that agreement forever, so re-recording the same paper document
    # with the right amount 409'd with no way out. Uniqueness still holds
    # where it matters — two LIVE receipts can't claim one ref.
    __table_args__ = (
        sa.Index("uq_agr_receipt_ref_per_agreement", "agreement_id", "receipt_ref",
                 unique=True,
                 postgresql_where=sa.text(
                     "receipt_ref IS NOT NULL AND status NOT IN ('voided', 'rejected')")),
    )
