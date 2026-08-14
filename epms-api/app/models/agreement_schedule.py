"""ORM model for agreement payment schedule rows (period + milestone).

一张表带 schedule_type 区分两种排期,因为两者结构高度相同(都是"预先排好的
若干期,每期等一张发票")。差异全在可空列上,建两张几乎一样的表不划算。

⚠️ accepted_by / accepted_at 是**双语义**列,由 schedule_type 决定含义:
  - period    → 履约确认("本期服务正常"),Phase 1B 实现
  - milestone → 阶段验收,Phase 1C 才实现,本期一律为 NULL
读这两列的代码必须先看 schedule_type。
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class AgreementPaymentSchedule(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "agreement_payment_schedule"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_agreements.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # period | milestone
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    # period 专用。milestone 永远 NULL —— 阶段时间是相对合同事件的,见 expected_timing。
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # milestone 专用。纯文本,如 "Within 1 week after contract signing"。
    expected_timing: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # pending | received | overdue | waived
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending", index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # period 专用
    period_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 百分数:5.00 = ±5%
    tolerance_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    overdue_after_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # milestone 专用
    milestone_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Phase 1C 的验收判定条件("设备验收合格")—— 与 expected_timing 的"什么时候"
    # 是两回事,本期不写、不在 UI 出现。
    trigger_condition: Mapped[str | None] = mapped_column(Text, nullable=True)

    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
