"""One person's choice of dashboard panels."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DashboardLayout(Base):
    """Which panels, in which order.

    An empty list means the default — so a panel added to the product later
    shows up for everybody instead of staying invisible until each person
    goes and ticks it.
    """

    __tablename__ = "ehs_dashboard_layouts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    panels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                         server_default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now())
