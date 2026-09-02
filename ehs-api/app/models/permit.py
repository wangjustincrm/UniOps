"""Work permits: confined space, hot work, height, lockout, excavation, electrical.

These are the jobs that kill people, and the permit is the control. Three
things in this model exist because of how permits fail in practice rather than
because of how they are drawn on a form.

**A permit expires by itself.** `valid_to` is checked on read, not only by the
sweep, because "the board still says ACTIVE" is how a crew re-enters a space on
yesterday's authorisation. The stored status is advisory; the effective status
is computed, and the two are reconciled by the sweep rather than trusted.

**Gas readings carry their own verdict, computed.** An entrant does not decide
whether 18.9% oxygen is acceptable — the standard does. So the limits live here
as named constants and `is_acceptable` is derived, never accepted from the
client. A permit with a failing reading cannot be issued.

**Signatures are per role and per phase.** Issuing and closing are different
acts by different people, and a confined space entry needs the issuer, the
receiver and the attendant separately. One signature column would collapse
three distinct legal responsibilities into "somebody signed".
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PermitType(UUIDPrimaryKey, TimestampMixin, Base):
    """A kind of permit, and what it demands before work starts.

    Configurable because the precaution list is the plant's, not the
    regulation's — Reg 632 says a confined space needs a plan, not which
    fourteen boxes this plant wants ticked.
    """

    __tablename__ = "ehs_permit_types"

    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{key, label, help}] — every one must be confirmed before issue.
    precautions: Mapped[list] = mapped_column(JSONB, nullable=False,
                                              default=list, server_default="[]")
    requires_gas_test: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                    default=False, server_default="false")
    # Which signatures this kind needs at issue, as role codes.
    required_signatures: Mapped[list] = mapped_column(JSONB, nullable=False,
                                                      default=list, server_default="[]")
    # A permit that never expires is not a permit. Null means "the shift it was
    # issued for", enforced as one working day.
    max_duration_hours: Mapped[int] = mapped_column(Integer, nullable=False,
                                                    default=12, server_default="12")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                            default=True, server_default="true")


class Permit(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_permits"

    permit_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_permit_types.id", ondelete="RESTRICT"),
        nullable=True, index=True)
    # Snapshot: a permit is evidence, and a renamed type must not rewrite it.
    type_label: Mapped[str] = mapped_column(String(150), nullable=False)

    work_description: Mapped[str] = mapped_column(Text, nullable=False)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                          nullable=True, index=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    equipment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_equipment.id", ondelete="SET NULL"), nullable=True)
    # The method statement this work follows, where there is one.
    jsa_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_jsa.id", ondelete="SET NULL"), nullable=True)

    contractor_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    requested_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                       nullable=True, index=True)

    # draft | issued | suspended | closed | cancelled | expired
    status: Mapped[str] = mapped_column(String(12), nullable=False,
                                        default="draft", server_default="draft", index=True)
    # {precaution_key: true} — every one on the type must be present to issue.
    precautions_confirmed: Mapped[dict] = mapped_column(JSONB, nullable=False,
                                                        default=dict, server_default="{}")

    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    issued_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    suspended_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # What the closer confirms: people out, equipment removed, area left safe.
    closeout_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    qr_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)


class PermitGasReading(UUIDPrimaryKey, Base):
    """One atmospheric test.

    `is_acceptable` is computed from the limits in app/services/permit.py and
    stored, never accepted from the caller. An entrant does not get to decide
    whether 18.9% oxygen is safe.
    """

    __tablename__ = "ehs_permit_gas_readings"

    permit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_permits.id", ondelete="CASCADE"),
        nullable=False, index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    taken_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    taken_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    oxygen_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    lel_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    h2s_ppm: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    co_ppm: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    other_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    is_acceptable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Which readings failed, so the record says why rather than just "no".
    failed: Mapped[list] = mapped_column(JSONB, nullable=False,
                                         default=list, server_default="[]")
    instrument: Mapped[str | None] = mapped_column(String(100), nullable=True)


class PermitSignature(UUIDPrimaryKey, Base):
    """Who signed, in what capacity, at which phase.

    Per role and per phase because issuing and closing are different acts by
    different people, and a confined-space entry needs the issuer, the receiver
    and the attendant separately. One signature column would collapse three
    distinct responsibilities into "somebody signed".
    """

    __tablename__ = "ehs_permit_signatures"

    permit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_permits.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # issue | close
    phase: Mapped[str] = mapped_column(String(10), nullable=False,
                                       default="issue", server_default="issue")
    # issuer | receiver | attendant | gas_tester | fire_watch
    capacity: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    person_name: Mapped[str] = mapped_column(String(255), nullable=False)
    person_role: Mapped[str | None] = mapped_column(String(150), nullable=True)
    external_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")
    signed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
