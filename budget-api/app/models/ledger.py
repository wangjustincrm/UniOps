"""Cross-service write ledger — idempotent record of every budget movement.

Acts as both the source of truth for actual/committed aggregation AND
the idempotency guarantee for cross-service writes.

Unique constraint (source_service, source_doc_type, source_doc_id, operation)
ensures duplicate POSTs from other services are silently ignored.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


LEDGER_OPS = {"commit", "release", "actualize", "book_expense", "opening"}

# Which operations make up each figure the Budget Dashboard shows. Named here
# because four places aggregate them and they must agree — and because the
# assistant is now asked what "actual" means on that screen, which is exactly
# this list and nothing else.
#   actualize     an invoice was posted against something already committed
#   book_expense  an expense claim booked straight to the budget
#   opening       actuals finance imported for the part of the year that
#                 happened before this ledger existed
ACTUAL_OPS = ("actualize", "book_expense", "opening")
#   commit adds, release and actualize give back: a commitment stops being a
#   commitment both when it is cancelled and when it turns into a real cost.
COMMIT_ADDS = ("commit",)
COMMIT_RELEASES = ("release", "actualize")


class BudgetLedger(UUIDPrimaryKey, Base):
    __tablename__ = "budget_ledger"
    __table_args__ = (
        UniqueConstraint(
            "source_service", "source_doc_type", "source_doc_id", "operation",
            name="uq_ledger_idempotency",
        ),
        CheckConstraint(
            "operation IN ('commit','release','actualize','book_expense','opening')",
            name="ck_ledger_op",
        ),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_ledger_month"),
    )

    source_service: Mapped[str] = mapped_column(String(30), nullable=False)
    source_doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    cost_center_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
