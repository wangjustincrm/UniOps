"""NC customer master (bd_customer direct import) — partner dimension lookup.

The ERP integration API has no customer endpoint (v1.1 doc verified;
'vendor' there is manufacturers), so customers import straight from NC.
finance owns this table (migration 0020); scripts/nc_migration/customers_import.py
fills it.
"""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class NcCustomer(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "nc_customers"

    code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
