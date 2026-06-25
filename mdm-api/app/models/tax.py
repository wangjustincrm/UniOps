"""Canadian sales tax master data + place-of-supply rules (Phase 0-B2).

FIN-TAX-001: tax codes with effective-dated rates and ITC recoverability.
FIN-TAX-002: determination rules — (direction × province × customer_type ×
item_tax_class) → tax code combo. NULL criterion = wildcard; higher priority
wins. NO tax treatment is hardcoded anywhere — "milk powder is zero-rated"
must be a rule row, never an if-statement (PRD §8.6 explicit warning).
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class TaxCode(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "tax_codes"
    __table_args__ = (
        # rate versioning: same code may have multiple effective-dated rows
        UniqueConstraint("code", "effective_from", name="uq_tax_codes_code_from"),
    )

    code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    tax_type: Mapped[str] = mapped_column(String(10), nullable=False)  # GST|HST|PST|RST|QST|NONE
    province: Mapped[str | None] = mapped_column(String(2), nullable=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 5), nullable=False)  # fraction: 0.05 = 5%
    recoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # ITC
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaxRule(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "tax_rules"

    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="any")  # purchase|sale|any
    province: Mapped[str | None] = mapped_column(String(2), nullable=True)             # NULL = any
    customer_type: Mapped[str | None] = mapped_column(String(20), nullable=True)       # business|consumer|export|NULL
    item_tax_class: Mapped[str | None] = mapped_column(String(20), nullable=True)      # standard|zero_rated|exempt|NULL
    tax_code_list: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)   # ["GST","PST_BC"]
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
