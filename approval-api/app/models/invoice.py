"""Read-write mirror of EPMS invoices — status update on PA approval only."""
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Invoice(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "invoices"

    status: Mapped[str] = mapped_column(String(20), nullable=False)
