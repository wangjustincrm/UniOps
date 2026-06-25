from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class UnitOfMeasure(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "units_of_measure"

    # Unit symbols keep their case (kg, m², L, pcs) — do NOT upper-case.
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # count | mass | volume | length | area | time | other
    dimension: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
