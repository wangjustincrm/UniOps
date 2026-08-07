"""Read-write mirror of EPMS purchase_agreements — workflow execution only.

Only the columns the engine touches are mirrored. Verified against
epms-api/app/models/agreement.py (the ORM source Base.metadata.create_all
builds the test schema from) and alembic/versions/ag01_purchase_agreements.py
before writing — mirror drift has bitten this project three times.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PurchaseAgreement(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "purchase_agreements"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # NTE is nullable — an agreement may be approved without a ceiling.
    not_to_exceed: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
