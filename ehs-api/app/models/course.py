"""Running a training course: one session, its roster, and who passed.

The catalogue (`ehs_courses`) says a course exists. This says it was *held* —
on a date, in a room, by an instructor, with these people in it. HSE gave the
sequence they follow, and the model is shaped to it: create the session and
attach the material, name a supervisor as its administrator, let that person
build the roster, remind the roster the day before, then mark each attendee
passed or failed.

Two decisions worth stating:

**The coordinator is stored on the session, not derived from a role.** HSE
nominates a different supervisor per course. Reading it from the roster or
from who created it would be wrong on the first course somebody else runs.

**Passing writes a training record; failing does not.** The record in a
person's file is the evidence an inspector asks for, and the enrolment keeps
`training_record_id` so the two can never disagree about whether it was
issued. Re-marking someone who already passed does not issue a second one.
"""
import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class CourseDelivery(UUIDPrimaryKey, TimestampMixin, Base):
    """One scheduled sitting of a course."""

    __tablename__ = "ehs_course_deliveries"

    delivery_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_courses.id", ondelete="RESTRICT"),
        nullable=True, index=True)
    # Snapshot, so a renamed course does not rewrite last year's sessions.
    course_label: Mapped[str] = mapped_column(String(200), nullable=False)
    # Defaults to the course name; overridden when a session covers a variant
    # ("WHMIS — refresher", "Forklift — counterbalance only").
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    session_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Separate columns because the sample forms ask them separately, and
    # because a session with no fixed end is normal.
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # True when the end was inferred from the first quiz pass rather than
    # entered. A record of length only: the join window ignores it. Round 6.
    end_time_from_quiz: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    location_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # The supervisor HSE puts in charge of this session's roster.
    coordinator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    coordinator_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Free text: often an external trainer who has no UniOps account. HSE fills
    # it in after the session, which is why it is nullable at creation.
    instructor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Course material — slide deck, handout — held in file-api.
    material_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    material_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # scheduled | completed | cancelled
    status: Mapped[str] = mapped_column(String(15), nullable=False,
                                        default="scheduled", server_default="scheduled",
                                        index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stamped when the day-before reminder went out, so the sweep — which runs
    # many times a day — sends it once rather than on every tick of that day.
    reminded_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class CourseEnrolment(UUIDPrimaryKey, TimestampMixin, Base):
    """One person on one session's roster, and how they did."""

    __tablename__ = "ehs_course_enrolments"
    __table_args__ = (
        UniqueConstraint("delivery_id", "user_id", name="uq_ehs_course_enrolment"),
    )

    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_course_deliveries.id", ondelete="CASCADE"),
        nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # roster | quiz. How they got on the list: added by whoever built it, or by
    # scanning the quiz code in the room. Kept apart from the marking columns,
    # which a hand mark overwrites — the person running the session needs to see
    # who let themselves in before closing it, whatever their result.
    joined_via: Mapped[str] = mapped_column(String(10), nullable=False,
                                            default="roster", server_default="roster")

    # enrolled | passed | failed | absent
    result: Mapped[str] = mapped_column(String(10), nullable=False,
                                        default="enrolled", server_default="enrolled",
                                        index=True)
    marked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    marked_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Whether they passed the session's quiz, and who said so: "Online quiz"
    # when they answered it here, or the person running the session for a
    # quiz done on paper. Round 6: passing the quiz is necessary, not
    # sufficient — the person running the session still marks the pass.
    quiz_passed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quiz_marked_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The record issued into this person's file when they passed. Set once.
    training_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_training_records.id", ondelete="SET NULL"),
        nullable=True)


class CourseMaterial(UUIDPrimaryKey, Base):
    """One file or link, on a course or on one session of it.

    A course is rarely one file — the deck, the handout, the sign-off sheet,
    last year's revision — and the single `material_file_id` on the course row
    could only ever hold the last one uploaded. That column stays, backfilled
    into this table as the first item, so nothing already attached was lost.

    Round 6: a session starts with a copy of its course's material (the same
    file, referenced again — app.services.materials decides when a file may
    actually be deleted), and either may hold a link instead of a file, which
    is how a video too large to upload is attached.
    """

    __tablename__ = "ehs_course_materials"
    __table_args__ = (
        CheckConstraint("num_nonnulls(course_id, delivery_id) = 1",
                        name="ck_ehs_course_material_owner"),
        CheckConstraint("num_nonnulls(file_id, url) = 1",
                        name="ck_ehs_course_material_kind"),
    )

    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_courses.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_course_deliveries.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # For a link, what it is called on screen.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
