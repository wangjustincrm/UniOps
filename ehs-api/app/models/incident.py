"""Incidents: injuries, equipment events and near misses.

One table backs all three of the forms the HSE Manager supplied, distinguished
by `form_kind`. They share a reporter, a place, a time, an investigation and a
set of causes; only the sections differ, and those differences live in the
per-section child tables rather than in three near-identical parent tables.

Two columns carry most of the regulatory weight, and the reason they are two
columns rather than one is worth stating plainly.

`injury_class` is one of first_aid / medical_aid / lost_time. `mol_reportable`
is a separate boolean covering critical injuries and fatalities. The earlier
design modelled severity as a single five-level list, which cannot express the
case the plant actually has: a lost-time injury that is also reportable to the
Ministry. The HSE Manager's own Medical Incident form is built the same way —
three incident types, with critical injuries routed down a separate branch
("Does this require a MOL injury report to be completed? Click here if so").
The two fields drive two independent clocks and must never be merged.

`occurred_at` and `employer_aware_at` are likewise separate, and for the same
kind of reason. The MOL written report is due 48 hours after the occurrence
(OHSA s.51(1)). WSIB must *receive* Form 7 within three business days of the
employer learning of the obligation. Those are different instants; collapsing
them into one column guarantees one of the two clocks is wrong, and does so
silently.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.common import EhsCommonDims


class Incident(UUIDPrimaryKey, EhsCommonDims, TimestampMixin, Base):
    __tablename__ = "ehs_incidents"
    __default_status__ = "draft"

    incident_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    # medical | equipment | near_miss — the three forms in use today.
    form_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # ── Reporting ───────────────────────────────────────────────────────────
    reported_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reported_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_anonymous: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    reported_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reported_to_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Classification: two orthogonal facts ────────────────────────────────
    # first_aid | medical_aid | lost_time. Null until classified, and null
    # forever on a near miss. Drives the WSIB clock and the injury rates.
    injury_class: Mapped[str | None] = mapped_column(String(15), nullable=True, index=True)
    # Critical injury or fatality. Independent of injury_class — an incident
    # can be both. Drives the MOL 48-hour clock.
    mol_reportable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True,
    )
    mol_reportable_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    classified_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── The two clock origins ───────────────────────────────────────────────
    # occurred_at comes from EhsCommonDims and starts the MOL clock.
    # This one starts the WSIB clock and is usually later.
    employer_aware_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ── Narrative ───────────────────────────────────────────────────────────
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    equipment_involved: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Round 12, INC-04/D3: the product batch code, beside the date on the report
    # and under who investigated on the investigation — one value, two places.
    batch_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # From the equipment register (round 10); the text above is for anything
    # the register does not list.
    equipment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_equipment.id", ondelete="SET NULL"),
        nullable=True, index=True)
    witnesses_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    immediate_action_taken: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Answers to the sections that differ per form_kind (the near-miss
    # "proactive notice" block, the equipment "additional impacts" block).
    # Structured, form-specific, and not worth a column each until they are
    # needed in a report.
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # ── Lifecycle ───────────────────────────────────────────────────────────
    # draft -> submitted -> under_investigation -> pending_closure -> closed
    # `status` and its default come from EhsCommonDims / __default_status__.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Approval engine columns (written by approval-api via its mirror) ─────
    # The engine is pointed at approval_status via _DOC_META's status_attr so
    # it never touches the lifecycle column above.
    approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Reopening a closed incident. Recorded rather than silent: the alternative
    # to an edit anybody can see is a second incident raised for the same
    # event, which is what people do when a record is frozen with no way back.
    reopened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    reopened_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reopened_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approval_step_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)

    signature: Mapped[str | None] = mapped_column(Text, nullable=True)  # base64 PNG
    # Snapshots taken at signing time. `signer_role` is a job title from the EHS
    # position tables, never the UniOps `role` column — that says what someone
    # may access, not what they do.
    # HSE's second pair of eyes. Separate from the investigation (a supervisor
    # may do that) and from closing (either may do that).
    # What the incident cost, and hours actually lost. Neither is derivable —
    # cost is a company judgement and lost hours come from payroll — so both are
    # entered after the fact and stay null until someone knows.
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    lost_time_hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    hse_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hse_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    hse_reviewed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signer_role: Mapped[str | None] = mapped_column(String(150), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IncidentPerson(UUIDPrimaryKey, TimestampMixin, Base):
    """People attached to an incident, including non-employees.

    The Medical form asks "If not a CRM employee, write the injured persons
    name and company here", so `user_id` is nullable and `person_name` is
    always populated — for employees it is the name snapshot, for everyone else
    it is all we have.
    """

    __tablename__ = "ehs_incident_persons"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # injured|involved|witness|first_aider
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    person_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # WSIB Form 7 fields. Vocabulary-backed so the injury register can be
    # grouped by body part and nature of injury.
    body_part_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    body_part_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nature_of_injury_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    nature_of_injury_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    treatment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the first aid register used to hold, on the person rather than the
    # incident: one incident can hurt two people differently, and "sent off
    # site" is true of a person, not of an event.
    sent_offsite: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    follow_up: Mapped[str | None] = mapped_column(Text, nullable=True)


class IncidentInvestigation(UUIDPrimaryKey, TimestampMixin, Base):
    """Section C of the Medical form; the Investigation block of the others."""

    __tablename__ = "ehs_incident_investigations"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    investigator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    investigator_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Supporting investigators — the Equipment form allows several.
    supporting_investigator_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    # Vocabulary item ids, so "which hazards recur" is a groupable question.
    identified_hazard_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    ppe_that_could_prevent: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    sequence_of_events: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Round 12, INC-15/16 and Q8: "the 5 why technique … any items categorized
    # as food safety would require it." Up to five answers, in order: why it
    # happened, why that happened, and so on to the root cause.
    five_whys: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    # ★ UAT 13, on a food-safety near miss: "a spreadsheet like, detailed
    # production chronology … production start time, the point at which the
    # deviation was detected, containment actions taken, production end time,
    # affected quantity, inspection activities, and current product status."
    # {rows: [{at, stage, detail, quantity}], affected_quantity, product_status}.
    production_chronology: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    signed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)


class Cause(UUIDPrimaryKey, TimestampMixin, Base):
    """An immediate or root cause, and the hinge the corrective actions hang off.

    `ehs_actions.cause_id` points here. That is what lets the investigation
    screen show a root cause with its actions and their completion state
    underneath it — and lets a root cause with no action under it read as
    visibly incomplete, which is exactly what an auditor looks for.
    """

    __tablename__ = "ehs_causes"
    # ★ Declared here as well as in the migration. The test database is built
    # with `create_all` from these models, so a constraint that lives only in a
    # migration does not exist while the tests run — a row with both owners, or
    # neither, would be accepted all the way through the suite and refused for
    # the first time in production. This repository has been caught by that
    # exact gap before, in `conftest` not building the authz foreign keys.
    __table_args__ = (
        CheckConstraint("num_nonnulls(incident_id, hazard_id) = 1",
                        name="ck_ehs_causes_one_owner"),
    )

    # ★ One of these two, never both and never neither — a database CHECK says so.
    # Round 5 put causes on the hazard register as well as on an investigation.
    #
    # Two nullable foreign keys rather than this module's usual polymorphic
    # `(source_type, source_id)` pair, which is what `ehs_actions` uses. Causes
    # come from an incident, a hazard, and one day a job analysis — three kinds,
    # against seven or eight for an action — and both of these keep their
    # `ON DELETE CASCADE`. Going polymorphic would have traded a working cascade
    # for one fewer column, and then needed a guard against the orphans it
    # created. Argued out with uniops-41 in the round-5 review, who was right.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    hazard_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_hazards.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    cause_type: Mapped[str] = mapped_column(String(12), nullable=False)  # immediate|root
    vocabulary_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Snapshot of the wording at the time — see app/models/vocabulary.py.
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # ★ UAT 13, on a food-safety investigation: "in each immediate cause
    # entered, beside the cause, add a pick list field for the name of the
    # department that completed the work and the date it was completed." The
    # name is kept as it read when picked, as every reference on a signed record.
    completed_by_department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    completed_by_department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class IncidentAttachment(UUIDPrimaryKey, Base):
    """A photograph (or document) attached to an incident report.

    The bytes live in file-api; this row is the join. file-api has no
    "list by document" endpoint, so the owning service is what knows which
    files belong to which record — the same arrangement `pr_attachments` uses.
    """

    __tablename__ = "ehs_incident_attachments"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
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


class IncidentEvent(UUIDPrimaryKey, Base):
    """Something done to an incident that its timeline has to show.

    ★ Round 9: "log who changed it, and the date in the timeline" (a change of
    the kind of incident), and "when reopening an incident, require a mandatory
    reason … log who reopened it, the date and the reason in the timeline."
    The timeline's other rows are single columns on the incident (classified,
    reviewed, closed); these can happen more than once, so they are rows.
    """

    __tablename__ = "ehs_incident_events"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # kind_changed | reopened | hse_reviewed | closed
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
