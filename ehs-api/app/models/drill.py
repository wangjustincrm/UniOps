"""Emergency drills, and the two numbers a drill exists to produce.

A fire drill is not a meeting that happened. It is a measurement: how long did
it take to get everyone out, and was everyone accounted for at the muster
point. Everything else on the record is context for those two.

So `evacuation_seconds` and `all_accounted_for` are first-class columns rather
than sentences in a notes field. A drill whose time got worse is the finding;
a drill where somebody was missing is a much bigger one, and neither is
searchable if it lives in prose.

`what_failed` is separate from `observations` for the same reason. Every drill
report ever written says "generally went well"; the useful half is the part
that did not, and giving it its own field is what stops it being softened into
the paragraph above it.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Drill(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_drills"

    drill_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    # fire | evacuation | spill | rescue | medical | shelter | other
    drill_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scenario: Mapped[str] = mapped_column(Text, nullable=False)

    planned_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    held_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Which areas were in scope, as a list of {id, path}. A plant-wide
    # evacuation is not one location, and recording it as the site root would
    # make "which areas have we drilled this year" unanswerable.
    areas: Mapped[list] = mapped_column(JSONB, nullable=False,
                                        default=list, server_default="[]")
    # Announced drills measure the procedure; unannounced ones measure the
    # people. Both are worth doing and they are not comparable, so the record
    # says which it was.
    is_announced: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                               default=True, server_default="true")

    participants_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The number. Seconds rather than minutes because the difference between
    # 3:10 and 3:50 is the finding, and minutes rounds it away.
    evacuation_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The other number. Nullable on purpose: "nobody checked" is a third state
    # and a worse one than "somebody was missing".
    all_accounted_for: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    missing_note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_worked: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Kept apart from `observations` so it cannot be softened into the
    # paragraph above it.
    what_failed: Mapped[str | None] = mapped_column(Text, nullable=True)

    # planned | held | evaluated
    # Why it went the way it did, from the same list the incident
    # investigations draw on, so a recurring cause shows up across both. Id and
    # label together: the id is what a report groups on, the label keeps an old
    # drill readable after the vocabulary entry is renamed.
    root_cause_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    root_cause_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Who ran it. Not the same person as the one who signs the record off, and
    # not readable from the signature — which is a picture of a name, and was
    # the only answer this record could give.
    run_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    run_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Who says this is what happened. On the row rather than in file-api: a
    # drill report that renders without its signature is not the document that
    # was signed.
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    signed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    signed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(12), nullable=False,
                                        default="planned", server_default="planned",
                                        index=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    evaluated_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DrillParticipant(UUIDPrimaryKey, Base):
    """Somebody who took part, and in what capacity.

    Optional — a plant-wide evacuation is recorded as a headcount, not four
    hundred rows. Named participation is for the people whose role in the drill
    is itself the thing being tested: wardens, first aiders, the incident
    controller.
    """

    __tablename__ = "ehs_drill_participants"

    drill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_drills.id", ondelete="CASCADE"),
        nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    person_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # participant | warden | first_aider | controller | observer
    capacity: Mapped[str] = mapped_column(String(20), nullable=False,
                                          default="participant", server_default="participant")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class DrillAttachment(UUIDPrimaryKey, Base):
    """A photograph taken at a drill — the muster point, a blocked exit.

    The bytes live in file-api; this row is the join, because file-api has no
    "list by document" endpoint and the owning service is what knows which
    files belong to which record. Same arrangement as incident attachments.
    """

    __tablename__ = "ehs_drill_attachments"

    drill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_drills.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    storage_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
