"""Shared column mixins for Safety records.

`EhsCommonDims` is the reason the reporting engine can be one engine driven by
configuration instead of a hand-written report per question. The HSE Manager's
requirement was explicit: build "a common reporting engine, rather than
creating each report separately", with consistent fields across Incident,
Hazard, Observation, Inspection, Audit, Corrective Action, Training and
Compliance.

That only works if every reportable table exposes the same dimensions under
the same names, so a report definition can name `department_id` without
knowing which table it will be run against. Adding these columns later would
mean rewriting every table and every stored report, so they land with the very
first migration.

Two of them are snapshots on purpose:

* `location_path` — the plant area tree is maintained by HSE and will be
  renamed and restructured over the years. A 2026 incident must still read as
  it did when it was signed, so the path is copied in at write time. Grouping
  in reports goes through `location_id`, which survives a rename; display goes
  through the snapshot.
* `category_label` — same reasoning for the vocabulary the record was filed
  under. See `app/models/vocabulary.py` for why entries are retired rather
  than deleted.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class EhsCommonDims:
    """The dimensions every reportable Safety record carries."""

    # ── When ────────────────────────────────────────────────────────────────
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ── Where ───────────────────────────────────────────────────────────────
    # No FK: `locations` is master data owned by mdm-api, and cross-service
    # references in this codebase are plain UUID columns by convention (same
    # as users.department_id).
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
    )
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
    )
    shift_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # ── What kind ───────────────────────────────────────────────────────────
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    category_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)

    # ── Lifecycle ───────────────────────────────────────────────────────────
    # Every reportable record has a status, and reports filter on it by name,
    # so it lives here rather than being spelled differently per table. The
    # value set differs by record type (an incident is draft/submitted/...,
    # an action is open/in_progress/...), so each table declares its own
    # starting value via __default_status__.
    @declared_attr
    def status(cls) -> Mapped[str]:  # noqa: N805
        return mapped_column(
            String(20),
            nullable=False,
            index=True,
            default=getattr(cls, "__default_status__", "draft"),
            server_default=getattr(cls, "__default_status__", "draft"),
        )

    # ── Who owns it ─────────────────────────────────────────────────────────
    # declared_attr because a ForeignKey object cannot be shared across the
    # several tables that mix this in — each subclass needs its own instance.
    @declared_attr
    def owner_id(cls) -> Mapped[uuid.UUID | None]:  # noqa: N805
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        )

    # Name snapshot: the record must still say who owned it after that person
    # leaves the company and their user row is deactivated or nulled out.
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
