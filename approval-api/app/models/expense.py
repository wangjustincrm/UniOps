"""Read-write mirror of expense_claims — approval-api updates status/step_idx only."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class ExpenseClaim(UUIDPrimaryKey, Base):
    __tablename__ = "expense_claims"

    claim_number: Mapped[str] = mapped_column(String(30), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(10), nullable=False)  # EXP|MIL|TRV|CFM*
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # employee_id column aliased as created_by so the engine can resolve dept_manager
    # (engine always uses doc.created_by for department/role lookups)
    created_by: Mapped[uuid.UUID] = mapped_column(
        "employee_id", UUID(as_uuid=True), nullable=False
    )

    @property
    def title(self) -> str:
        return f"{self.claim_type} Claim"
