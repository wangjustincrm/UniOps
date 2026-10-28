"""HSE news, on everybody's My Actions.

Round 10 (open box): "Create a news section on the my action register for the
hse role. on this page create a large text box where the hse role can input
news items that are important for staff to see and know. the layout should be
as follows: Date, Time, Importance rating (high, medium, low), and Message …
for all staff create a news ticker on the my actions dashboard at the top of
the page that will get information from the HSE role news item in the same
layout." Plain text: it is shown to everybody, so nothing in it is rendered.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class NewsItem(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_news"
    __table_args__ = (
        CheckConstraint("importance IN ('high', 'medium', 'low')", name="ck_ehs_news_importance"),
    )

    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    importance: Mapped[str] = mapped_column(String(6), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    posted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    posted_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Taken down: off the ticker, kept for the record.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
