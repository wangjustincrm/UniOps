"""ORM model for Invoice."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, case
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Invoice(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "invoices"

    internal_ref: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    vendor_invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)          # pre-tax
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # unmatched | matched | match_review | exception | approved | paid
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unmatched", index=True)

    line_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # PO / GR links (set at match time)
    po_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    gr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goods_receipts.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )
    gr_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # All linked GR IDs (UUID strings); gr_id/gr_number hold the first for backward compat
    gr_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Agreement route (blanket/house-account/contract spend) ────────────────
    # Mutually exclusive with the PO route in practice, but both columns are kept
    # nullable so a re-match can flip an invoice from one route to the other.
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_agreements.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )
    agreement_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # house_account | recurring | milestone — snapshot of the linked
    # agreement's type, written alongside agreement_number/agreement_id at
    # match time (Task 8 fix round 1, Important 1). Lets ANY caller who can
    # read this invoice at all learn whether it's on the house_account route
    # (the only one with receipt evidence) without a second, more narrowly
    # gated request to the agreement itself — see ag05_invoice_agreement_type.
    agreement_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 认领到的排期行(recurring 自动 FIFO / milestone 人工选)。
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 本次对账覆盖的协议凭证集合 —— 对应 gr_ids 的角色。**数组**:逐笔发票下长度
    # 为 1,月结汇总单下长度为 N。用单值会把"一张发票只对一份凭证"这个供应商的
    # 偶然事实固化成模型(设计 §2.3)。
    receipt_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 凭证合计与发票金额的差额说明。**与 legacy_settlement_reason 分开存**:
    # "对上了但差几块"与"根本没有凭证"是两件事,混存会让 legacy 计数失去意义。
    receipt_variance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Carrier of a courier invoice (match_route='freight'); NULL otherwise.
    freight_carrier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("freight_carriers.id", ondelete="RESTRICT"),
        nullable=True, index=True)
    # "po" | "agreement" | "freight" — which candidate pool this invoice was matched against.
    match_route: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # True only when the system resolved the route itself (1B). A human override
    # sets it False, which doubles as a health signal: a vendor whose route is
    # constantly corrected by hand has a mis-registered vendor_reference.
    match_route_auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    # ⚠️ Backlog escape hatch: paid against an agreement with NO receipt
    # evidence. Opened for the 1A invoice backlog; MUST be narrowed once 1B ships
    # receipt reconciliation, or it becomes the standard way to bypass matching.
    legacy_settlement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    legacy_settlement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 3-way match results
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    matched_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    matched_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    po_total: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    gr_value: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    variance: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    # Selected PO line IDs used as the match reference (null = entire PO)
    matched_po_line_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    matched_reference_total: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)

    # Exception handling
    exception_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    exception_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exception_resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    exception_resolved_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # accepted | credit_note_requested
    exception_resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Multi-PO line-level allocations (set at match time)
    allocations: Mapped[list["InvoicePoAllocation"]] = relationship(  # noqa: F821
        "InvoicePoAllocation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # ── For the Assistant's ontology (ISS-0013) ───────────────────────────────
    # "Which invoices are matched but not received?" was answered with every
    # invoice lacking a GR number — agreement invoices included, which never
    # have one and are backed by agreement receipts instead. These two say
    # which route an invoice is on and, for the PO route only, whether the PO
    # has receipt evidence under the SAME rule as the PA receipt gate.
    # Query-side only; the instance side is not used.
    @hybrid_property
    def billed_against(self) -> str:
        return ("agreement" if self.agreement_id else
                "purchase_order" if self.po_id else "none")

    @billed_against.inplace.expression
    @classmethod
    def _billed_against_expression(cls):
        return case((cls.agreement_id.is_not(None), "agreement"),
                    (cls.po_id.is_not(None), "purchase_order"), else_="none")

    @hybrid_property
    def po_receipt_recorded(self) -> bool | None:
        return None

    @po_receipt_recorded.inplace.expression
    @classmethod
    def _po_receipt_recorded_expression(cls):
        from app.core.receipt_evidence import receipt_evidence
        # NULL off the PO route: "not applicable", never "not received".
        return case((cls.agreement_id.is_(None) & cls.po_id.is_not(None),
                     receipt_evidence(cls.po_id)), else_=None)
