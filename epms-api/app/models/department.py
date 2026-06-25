"""Department ORM model."""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Department(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # back-ref populated when CostCenter model is loaded
    cost_centers: Mapped[list["CostCenter"]] = relationship(  # noqa: F821
        "CostCenter", back_populates="department", lazy="selectin"
    )
