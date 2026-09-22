"""Section B of the Medical form — injury detail WSIB requires.

Kept out of `ehs_incidents` deliberately: this is health information under
PHIPA, and a separate table is what lets `ehs.incident.medical.read` be a real
boundary instead of a convention every new endpoint has to remember.
"""
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class IncidentMedical(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_incident_medical"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )

    # Both halves. The id is who the follow-up goes to; the name is the
    # snapshot that keeps the record readable once that person has left and
    # the FK has gone to NULL.
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    supervisor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    type_of_injury: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # SAMPLE order — signs, allergies, medicines, past history, last meal,
    # events. A first aider recites the mnemonic; re-sorting these makes them
    # skip one.
    assessed_at_text: Mapped[str | None] = mapped_column(String(50), nullable=True)
    signs_symptoms: Mapped[str | None] = mapped_column(Text, nullable=True)
    allergies: Mapped[str | None] = mapped_column(Text, nullable=True)
    medicines: Mapped[str | None] = mapped_column(Text, nullable=True)
    past_medical_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_meal: Mapped[str | None] = mapped_column(Text, nullable=True)
    events_prior: Mapped[str | None] = mapped_column(Text, nullable=True)
    general_comments: Mapped[str | None] = mapped_column(Text, nullable=True)

    vitals_at_text: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pulse: Mapped[str | None] = mapped_column(String(20), nullable=True)
    respirations: Mapped[str | None] = mapped_column(String(20), nullable=True)
    blood_pressure: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pain_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    pain_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vitals_other: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Vitals are taken more than once, and the trend is what decides whether
    # somebody goes to hospital: a pulse of 110 reads one way alone and another
    # beside the 90 from five minutes earlier. The seven columns above are the
    # first reading, backfilled into this list by migration 0031 and no longer
    # written; they are kept because dropping columns on live injury records is
    # a risk with no upside.
    vitals_series: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]")

    timeline: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                           server_default="[]")

    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    recorded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
