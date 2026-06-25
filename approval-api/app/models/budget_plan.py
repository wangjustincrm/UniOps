"""Read-write mirror of budget-api budget_plans — workflow execution only.

approval-api updates `status`, `approval_step_idx`, `submitted_at`, `approved_at`
during plan lifecycle. budget-api owns the rest of the schema.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class BudgetPlan(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "budget_plans"

    cost_center_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # ── Revision lineage (owned by budget-api; approval-api flips is_current
    #    on the post-approve hook for revisions) ─────────────────────────────
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,  # FK in DB only, no ORM relationship
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Synthetic attributes for _DOC_META compatibility ─────────────────────
    # _DOC_META uses number_attr / amount_attr / vendor_attr to format task display
    # strings. Plans don't have a vendor or a single amount; we expose computed
    # properties so existing engine code (Task creation, etc.) works unchanged.

    @property
    def plan_number(self) -> str:
        # e.g., "BP-FY2026"; cost-center code/name is resolved client-side
        return f"BP-FY{self.fiscal_year}"

    @property
    def title(self) -> str:
        return f"Budget Plan FY {self.fiscal_year}"

    @property
    def plan_amount(self) -> Decimal:
        # Total plan amount could be computed from plan_lines, but that's expensive
        # on every task creation. Return 0; UI shows the breakdown separately.
        return Decimal("0")

    @property
    def cc_label(self) -> str:
        # vendor_attr stand-in. The plan owner doesn't have a vendor; show CC id.
        return f"CC {self.cost_center_id}"
