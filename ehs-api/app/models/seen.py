"""What each person has already looked at, so the menu can say what is new.

Round 12, ACT-01/02/03, ACT-04 and INC-08: Kirk asked for the flag the
feedback entry has — "similar to the new feedback indicator" — on My Actions
for new work, new news and, for HSE and area supervisors, new incidents; and
on Feedback for somebody whose comment has been answered.

That flag was a property of the record (a comment's status is "new" until HSE
looks), which is right for one shared queue and wrong for these: a news item
is new to everybody until each of them has seen it. So "seen" is per person,
here, and the counts and My Actions read the same rows.

★ Not a task. A flag that clears when the thing is opened is not work, and a
task nobody can complete never clears — the rule this module was built on.

★ A baseline per person, set the first time they are counted: everything
older is not new. Without it the first day would show every open item to
every person as new, which teaches people to ignore the flag on day one.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SeenMark(Base):
    """One person has opened one thing."""

    __tablename__ = "ehs_seen"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # task | incident | feedback
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class SeenBaseline(Base):
    """Where "new" starts for one person, and when they last read the news."""

    __tablename__ = "ehs_seen_baselines"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    news_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
