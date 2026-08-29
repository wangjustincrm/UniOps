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
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
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
    approval_step_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)

    signature: Mapped[str | None] = mapped_column(Text, nullable=True)  # base64 PNG


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
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    signed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)


class IncidentCause(UUIDPrimaryKey, TimestampMixin, Base):
    """An immediate or root cause, and the hinge the corrective actions hang off.

    `ehs_actions.cause_id` points here. That is what lets the investigation
    screen show a root cause with its actions and their completion state
    underneath it — and lets a root cause with no action under it read as
    visibly incomplete, which is exactly what an auditor looks for.
    """

    __tablename__ = "ehs_incident_causes"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    cause_type: Mapped[str] = mapped_column(String(12), nullable=False)  # immediate|root
    vocabulary_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Snapshot of the wording at the time — see app/models/vocabulary.py.
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
