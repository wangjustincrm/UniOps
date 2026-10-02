"""A question someone marked in the assistant panel, to ask again with one click.

See alembic/versions/as02_assistant_saved_q.py."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Per person. Enough for the handful of reports someone runs every week; a list
# much longer than this stops being one click and becomes a search.
MAX_SAVED_QUESTIONS = 10


class AssistantSavedQuestion(Base):
    __tablename__ = "assistant_saved_questions"
    __table_args__ = (
        UniqueConstraint("user_id", "question", name="uq_assistant_saved_q_user_question"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Not a foreign key, same as assistant_usage: users live in identity, and
    # this is a convenience list nothing else joins to.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True)
    # Same bound as a chat message, so anything that could be asked can be kept.
    question: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
