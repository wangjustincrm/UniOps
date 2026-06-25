"""ORM model for Project."""
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Project(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "projects"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    budget: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner_dept: Mapped[str | None] = mapped_column(String(50), nullable=True)
    manager_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
