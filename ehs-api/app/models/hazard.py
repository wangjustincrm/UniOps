"""The hazard register, and the job safety analyses that work through one job.

Two records that look similar and are not.

A **hazard** is a standing fact about the plant: there is a pinch point on the
case sealer, and it is there whether or not anybody is working today. It is
owned, reviewed, and either controlled or not. The register is what an auditor
reads to see that the employer knows what is dangerous about its own site.

A **JSA** is a job broken into steps, with the hazards of each step and the
controls for them, signed by the crew before they start. It is written once and
re-read every time that job is done, which is why it is versioned and why the
signatures are per person per version — a crew signing last year's method
statement for this year's work is the failure this guards against.

The link between them is deliberate and one-way: a JSA step may cite a hazard
from the register, so the register accumulates where each hazard actually
appears. It does not work the other way — deleting a JSA must not take a
standing hazard with it.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Hazard(UUIDPrimaryKey, TimestampMixin, Base):
    """One known hazard, where it is, and what is being done about it."""

    __tablename__ = "ehs_hazards"

    hazard_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # From the `hazard` vocabulary — 85 entries in HSE's spreadsheet, two
    # levels. Snapshot the label per the module-wide rule.
    hazard_type_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    hazard_type_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                          nullable=True, index=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    equipment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_equipment.id", ondelete="SET NULL"), nullable=True)

    # The current assessment. A rating is kept on the hazard as well as on the
    # assessment row so the register can be sorted by risk without a join per
    # line — the register is read as a ranked list, and that is its whole use.
    risk_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_risk_assessments.id", ondelete="SET NULL"),
        nullable=True)
    current_rating: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    residual_rating: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ★ What was found when somebody went and looked, as distinct from what the
    # hazard is. Round 5 put the investigation's apparatus on the register: an
    # incident records this under "What happened", and a hazard had nowhere for
    # it.
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_controls: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which level of the hierarchy the controls sit at — elimination through
    # PPE. An auditor asks this, and "we gave them gloves" reads differently
    # beside "we removed the pinch point".
    control_level: Mapped[str | None] = mapped_column(String(20), nullable=True)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    identified_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_due_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # open | controlled | eliminated. `controlled` is not `closed`: a guarded
    # pinch point is still a pinch point, and it comes back round for review.
    status: Mapped[str] = mapped_column(String(12), nullable=False,
                                        default="open", server_default="open", index=True)
    # Where it came from — an incident investigation, an inspection, a JSA, or
    # somebody walking round. Polymorphic, no FK, as everywhere else here.
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class Jsa(UUIDPrimaryKey, TimestampMixin, Base):
    """A job safety analysis: one job, its steps, and who has signed it."""

    __tablename__ = "ehs_jsa"

    jsa_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    equipment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_equipment.id", ondelete="SET NULL"), nullable=True)

    # PPE required for the whole job, over and above anything a step names.
    required_ppe: Mapped[str | None] = mapped_column(Text, nullable=True)

    # draft | active | retired
    status: Mapped[str] = mapped_column(String(10), nullable=False,
                                        default="draft", server_default="draft", index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The version this one replaced, so a signature on an older method
    # statement can be traced forward.
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_jsa.id", ondelete="SET NULL"), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class JsaStep(UUIDPrimaryKey, Base):
    """One step of the job: what is done, what can go wrong, what stops it.

    Three columns rather than a free-text method statement, because that is
    the shape of the analysis and because a step with a hazard and no control
    is the thing worth finding. A paragraph hides it; a row with an empty
    column does not.
    """

    __tablename__ = "ehs_jsa_steps"

    jsa_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_jsa.id", ondelete="CASCADE"),
        nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    step: Mapped[str] = mapped_column(Text, nullable=False)
    hazards: Mapped[str | None] = mapped_column(Text, nullable=True)
    controls: Mapped[str | None] = mapped_column(Text, nullable=True)
    control_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # A step may cite the standing register rather than restate a hazard.
    hazard_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_hazards.id", ondelete="SET NULL"), nullable=True)
    residual_rating: Mapped[str | None] = mapped_column(String(20), nullable=True)


class JsaSignoff(UUIDPrimaryKey, Base):
    """One person confirming they have read this version before starting.

    Per person per version, so signing v2 does not carry to v3. `worked_on`
    separates the crew who signed it today from the crew who signed it in
    March: the same JSA is signed afresh each time the job runs.
    """

    __tablename__ = "ehs_jsa_signoffs"
    __table_args__ = (
        UniqueConstraint("jsa_id", "user_id", "worked_on", name="uq_ehs_jsa_signoff"),
    )

    jsa_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_jsa.id", ondelete="CASCADE"),
        nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    person_name: Mapped[str] = mapped_column(String(255), nullable=False)
    person_role: Mapped[str | None] = mapped_column(String(150), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The day the job was done, not the day the record was typed up.
    worked_on: Mapped[date] = mapped_column(Date, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")
    is_supervisor: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                default=False, server_default="false")
