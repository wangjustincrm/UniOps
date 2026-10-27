"""The Joint Health and Safety Committee: roster, meetings, and the 21-day clock.

OHSA s.9 is the reason this exists, and it is the part of the module with a
statutory deadline attached to something other than an injury: when the
committee makes a written recommendation, the employer has twenty-one days to
respond in writing. That clock is not modelled here — it goes into
`ehs_statutory_deadlines` with the rest, so it is chased by the same sweep and
appears on the same compliance calendar.

Committee size at this plant is twenty-three, twenty of them certified, and
they meet monthly. Attendance is a row per member per meeting rather than a
count, because "who was absent three months running" is a real question and a
count cannot answer it.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class JhscMember(UUIDPrimaryKey, TimestampMixin, Base):
    """Somebody's seat on the committee.

    `representation` is the one field an inspector checks first: OHSA requires
    at least half the committee to be workers who do not exercise managerial
    functions. A count of members cannot answer that; this can.
    """

    __tablename__ = "ehs_jhsc_members"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # worker | management
    representation: Mapped[str] = mapped_column(String(12), nullable=False,
                                                default="worker", server_default="worker")
    # member | co_chair | secretary
    committee_role: Mapped[str] = mapped_column(String(12), nullable=False,
                                                default="member", server_default="member")
    is_certified: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                               default=False, server_default="false")
    certified_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    term_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    term_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                            default=True, server_default="true", index=True)


class JhscMeeting(UUIDPrimaryKey, TimestampMixin, Base):
    """A monthly meeting: agenda beforehand, minutes afterwards.

    Publishing is a distinct act from holding: minutes go to management, to
    every member, and onto the noticeboard, and until that has happened the
    meeting is not discharged. `published_at` is what the dashboard counts.
    """

    __tablename__ = "ehs_jhsc_meetings"

    meeting_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    held_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location_text: Mapped[str | None] = mapped_column(String(200), nullable=True)

    agenda: Mapped[str | None] = mapped_column(Text, nullable=True)
    minutes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The department this month's post-meeting inspection covers — HSE rotates
    # one department per month rather than picking at random.
    inspection_department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True)
    inspection_department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # scheduled | held | published
    status: Mapped[str] = mapped_column(String(12), nullable=False,
                                        default="scheduled", server_default="scheduled",
                                        index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    minutes_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # The minutes as a filled-in form, when a template is configured. Older
    # meetings keep the free-text `minutes` above and still read: configuring a
    # template changes what the next meeting is offered, not what the last one
    # recorded.
    minutes_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_form_submissions.id", ondelete="SET NULL"),
        nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class JhscAttendance(UUIDPrimaryKey, Base):
    """One member, one meeting, present or not."""

    __tablename__ = "ehs_jhsc_attendance"
    __table_args__ = (
        UniqueConstraint("meeting_id", "member_id", name="uq_ehs_jhsc_attendance"),
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_jhsc_meetings.id", ondelete="CASCADE"),
        nullable=False, index=True)
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_jhsc_members.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    representation: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # present | absent | regrets
    attendance: Mapped[str] = mapped_column(String(10), nullable=False,
                                            default="present", server_default="present")


class JhscRecommendation(UUIDPrimaryKey, TimestampMixin, Base):
    """A written recommendation, and the reply the employer owes it.

    OHSA s.9(20): the employer shall respond in writing within twenty-one days.
    The countdown itself lives in `ehs_statutory_deadlines`; `responded_at` here
    is what satisfies it.
    """

    __tablename__ = "ehs_jhsc_recommendations"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_jhsc_meetings.id", ondelete="CASCADE"),
        nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    raised_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Round 8 (open box): "when making a recommendation, include the 'where it
    # happened' field picker and include this info on the email." The path is
    # a snapshot, as on an incident; no FK, as locations belong to mdm-api.
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Round 10, TC-11: where the work stands, from the "JHSC recommendation
    # status" list HSE keeps under Settings → Lists. The label is a snapshot.
    status_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Who it was put in front of, and when. The committee could see that
    # nobody had answered and not who had failed to.
    #
    # ★ This does not start the twenty-one day clock — that runs from when the
    # recommendation is recorded. If sending started it, not sending would
    # extend a statutory deadline indefinitely.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    sent_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # [{id, name}] — names as well as ids, so the record stays readable after
    # somebody leaves.
    sent_to: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]")

    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    responded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # accepted | declined | deferred — recorded with the reply, because a
    # response that does not say what was decided is not a response.
    outcome: Mapped[str | None] = mapped_column(String(12), nullable=True)

    # Set when the reply commits to doing something; the corrective action is
    # how "we will fix it" stops being a sentence in a minute book.
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_actions.id", ondelete="SET NULL"), nullable=True)
