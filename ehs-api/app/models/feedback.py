"""Somewhere to say "this is wrong" about anything at all.

Round 4: "add a general comments feedback place to allow for general comments
for other issues found." Every other record in this module is about a specific
obligation — an incident, an inspection, a corrective action — and a person who
notices that a screen is confusing, a list is missing somebody, or a form asks
the wrong question has nowhere to put that. What happened instead is that it
arrived in the answer box of an acceptance script, or not at all.

★ This is deliberately not a task. Nobody is assigned a comment, and a task
nobody can complete never clears — the rule this module was built on. HSE is
told by email and the list carries a count of what has not been looked at.

★ Append-only in spirit rather than by trigger: the comment itself is never
edited, because rewording somebody's complaint is how a complaint disappears.
What HSE adds is a separate reply and a status beside it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Feedback(UUIDPrimaryKey, TimestampMixin, Base):
    """One comment, from one person, about anything."""

    __tablename__ = "ehs_feedback"

    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Where they were when they said it. Filled in by the screen rather than
    # typed: "the list is wrong" is unactionable without knowing which list.
    page_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True)
    # Snapshot, as everywhere else here: a comment from somebody who has since
    # left still has to say who made it.
    submitted_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # new → looked at → done. Not a workflow; a way for HSE to stop seeing the
    # ones they have dealt with.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="new", server_default="new")
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    answered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    answered_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
