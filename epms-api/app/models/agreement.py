"""ORM model for Purchase Agreement (AGR) — the blanket-PO replacement.

An agreement is the authorisation + price container for spend that must not go
through PR→PO→GR: house accounts (staff pick up at the vendor and the vendor
bills monthly) and contract-driven recurring / milestone payments. It is NOT an
order: it carries no quantities and is never received against.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PurchaseAgreement(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "purchase_agreements"

    number: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # house_account | recurring | milestone — only house_account is wired in 1A.
    agreement_type: Mapped[str] = mapped_column(String(20), nullable=False)

    contract_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 供应商侧的账号/引用号。切换时把现有 Open PO 号登记于此,供应商无需改号,
    # 其发票上印的老号仍能解析到本协议(1B 的自动识别用)。
    vendor_reference: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    # 过期后仍可匹配的宽限窗口 —— 月结账单总在期末之后才到(8/31 到期,9/3 来票)。
    grace_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")

    # NTE 只预警不拦截(用户决策):这两列驱动进度条与阈值通知,不阻断任何写路径。
    not_to_exceed: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    consumed_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default="0")

    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="CAD")
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)

    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    budget_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 协议责任人 —— NTE 预警与到期提醒的收件人。
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)

    # draft | in_review | returned | active | expired | closed | cancelled
    # "returned" is produced by the approval engine on a return action (see
    # approval-api/app/crud/engine.py "agr" entry) — EDITABLE_STATUSES accepts it.
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft", index=True)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
