"""Incident types HSE add themselves (round 12, GEN-01 and Q10).

"HSE can add new types and set the label, form, category and who can pick
them, but not the legal flags." The thirteen built-in types stay in
services/incident_type.py: they decide the Ministry and WSIB clocks and the
injury rates, and are not edited here. A type added here never carries an
injury class or a Ministry flag; how badly anyone was hurt is asked on the
form, as for the built-in types on it.

Retired, never deleted: an incident names its type by code.
"""
import uuid

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class CustomIncidentType(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_incident_types"

    code: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    # medical | equipment | near_miss — which of the three forms offers it.
    form_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    category_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # None: everyone who can report. Otherwise the permission whose holders may.
    pick_permission: Mapped[str | None] = mapped_column(String(60), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                            server_default="true")
