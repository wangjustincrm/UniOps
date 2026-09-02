"""Training quizzes, taken on a phone by scanning a code.

HSE asked for this as the end of a training session: the trainer puts a QR code
on the screen, everybody scans it and answers on their own phone, and the score
lands in their training record.

The one thing this model exists to enforce is that **the answer key never
leaves the server**. `correct` lives on the question row and is stripped by the
schema the learner receives; scoring happens here. A quiz whose answers are in
the page source is not a test of anything, and it would be an easy thing to
build by accident — the obvious implementation sends the whole question object.

Attempts are kept, all of them. Somebody who failed twice and passed on the
third attempt is a different training record from somebody who passed first
time, and an inspector asking "how do you know they understood it" is owed the
real history rather than the flattering one.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Quiz(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_quizzes"

    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_courses.id", ondelete="CASCADE"),
        nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    pass_mark_percent: Mapped[int] = mapped_column(Integer, nullable=False,
                                                   default=80, server_default="80")
    # Null means unlimited. HSE's view is that a quiz is a teaching aid, not a
    # gate, so retries are the default — but a course may cap them.
    max_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                            default=True, server_default="true")
    # What the trainer puts on the screen at the end of the session.
    qr_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class QuizQuestion(UUIDPrimaryKey, Base):
    """One question. `correct` is server-side only — see the module docstring."""

    __tablename__ = "ehs_quiz_questions"

    quiz_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_quizzes.id", ondelete="CASCADE"),
        nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # single | multi | true_false
    kind: Mapped[str] = mapped_column(String(12), nullable=False,
                                      default="single", server_default="single")
    # [{"key": "a", "label": "..."}]
    options: Mapped[list] = mapped_column(JSONB, nullable=False,
                                          default=list, server_default="[]")
    # The keys that are right. Never serialised to a learner.
    correct: Mapped[list] = mapped_column(JSONB, nullable=False,
                                          default=list, server_default="[]")
    # Shown after submitting, so a wrong answer teaches something.
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class QuizAttempt(UUIDPrimaryKey, TimestampMixin, Base):
    """One person's go at a quiz. Every attempt is kept, not just the best."""

    __tablename__ = "ehs_quiz_attempts"

    quiz_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_quizzes.id", ondelete="CASCADE"),
        nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Which session it was taken at, when it was taken at one.
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_course_deliveries.id", ondelete="SET NULL"),
        nullable=True)

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")
    # {question_id: [keys]} — kept so a trainer can see which question the room
    # got wrong, which is the useful output of running one of these.
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False,
                                          default=dict, server_default="{}")
    score_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    points_scored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    points_possible: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                         default=False, server_default="false", index=True)
    # Written when a pass produces a register entry. Set once.
    training_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_training_records.id", ondelete="SET NULL"),
        nullable=True)
