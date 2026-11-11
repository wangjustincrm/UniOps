"""Module configuration and the Ontario statutory holiday calendar."""
import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, Date, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EhsConfig(TimestampMixin, Base):
    """Single-row settings table (id is pinned to 1 by a CHECK constraint).

    Everything here is editable by the HSE Manager in Settings. What is
    deliberately NOT here: the 48-hour, three-business-day and 21-day limits
    themselves. Those are set in law and live as constants beside the section
    they cite. Only the holiday list they depend on is maintained.
    """

    __tablename__ = "ehs_config"
    __table_args__ = (CheckConstraint("id = 1", name="ck_ehs_config_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Which form template the JHSC minutes are written on. Null means the
    # free-text box, which is what every meeting used before this existed.
    jhsc_minutes_template_code: Mapped[str | None] = mapped_column(
        String(40), nullable=True)

    # ── Corrective action escalation (HSE Manager's schedule) ───────────────
    capa_remind_before_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3",
    )
    capa_escalate_supervisor_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5",
    )
    capa_escalate_manager_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10",
    )

    # ── Certificate expiry warnings, in days before expiry ──────────────────
    # What an in-house certificate says. The wording is the plant's own; who
    # earned it, which course, when and until when are not configurable,
    # because those are what make it evidence rather than decoration.
    certificate_heading: Mapped[str | None] = mapped_column(String(120), nullable=True)
    certificate_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    certificate_signatory: Mapped[str | None] = mapped_column(String(150), nullable=True)

    cert_warn_days: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=lambda: [90, 60, 30], server_default="[90, 60, 30]",
    )

    allow_anonymous_report: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )

    # ── Statutory deadline scanner ──────────────────────────────────────────
    # Re-read on every tick, so a change takes effect within the minute without
    # a restart. 0 disables the scanner entirely.
    statutory_scan_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60",
    )

    # ── Notification routing ────────────────────────────────────────────────
    # {incident_category_code: [user_id, ...]} — who is emailed when an
    # incident of that category is raised.
    incident_notify_groups: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    # {event: [user_id, ...]} — who is emailed when an incident's status
    # changes. Events in app.services.notify.STATUS_EVENTS. Empty means nobody:
    # these changes sent nothing before the list existed. Round 5, TC-15.
    incident_status_notify: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    # {list key: {"reporter": bool, "supervisor": bool}} — whether a list also
    # reaches the person who reported the incident and their supervisor. Keys:
    # an incident type, "*", or "status:<event>". Round 6, TC-07.
    incident_notify_include: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    # ★ Round 9: "create a 'who is told' for the JHSC recommendation when a
    # root cause corrective action form is completed." {"people": [user_id],
    # "sender": bool} — the people named, and whether whoever sent the
    # recommendation is told too (on unless HSE turns it off).
    # Round 12, WHO-01 / TC-13 / D13: who is told when a corrective action is
    # given to somebody — raised, reassigned or handed over from a leaver. The
    # owner always; these on top.
    action_assigned_notify: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
        default=lambda: {"people": [], "previous_owner": True, "reporter": False, "supervisor": False},
        server_default='{"people": [], "previous_owner": true, "reporter": false, "supervisor": false}',
    )
    jhsc_answered_notify: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=lambda: {"people": [], "sender": True},
        server_default='{"people": [], "sender": true}',
    )
    email_templates: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )

    # ── Lost-time classification rules ──────────────────────────────────────
    # The HSE Manager's form defers this to "as defined by WSIB", and the
    # boundary cases (modified duties at regular pay, at reduced pay, beyond
    # seven days) are still open with them. Isolating the rules here means the
    # answer changes one JSONB value and one pure function — not the schema.
    lost_time_rules: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )

    # ── The dashboard, for everybody (round 10, batch 4) ────────────────────
    # {metric: {"good": n, "bad": n}}. Which way is better is the metric's own
    # (services/dashboard_settings.py), not inferred from which number is
    # larger. The days-since figure starts at Kirk's example; a starting value.
    dashboard_thresholds: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=lambda: {"days_since": {"good": 15, "bad": 5}},
        server_default='{"days_since": {"good": 15, "bad": 5}}',
    )
    # {chart: "bar" | "circle" | "body"}; a chart not named draws its default.
    dashboard_chart_styles: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
    )
    # Round 12, Q16: how long an email's words are kept. 0 keeps them for good.
    communication_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=21, server_default="21",
    )
    # Round 12, ACT-05 / D12: how a due date on My Actions is coloured —
    # green within `green` days, yellow within `yellow`, red within `red`.
    due_bands: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=lambda: {"green": 10, "yellow": 5, "red": 2},
        server_default='{"green": 10, "yellow": 5, "red": 2}',
    )


class Holiday(Base):
    """Ontario statutory holidays.

    The WSIB clock is three *business* days, so it must skip these. Postgres
    has no holiday table, which is exactly why the deadline is computed in
    Python and stored as an absolute timestamp rather than expressed as a
    generated column.

    Seeded five years ahead by migration. Topping this up is an annual
    operations task — if it lapses, WSIB deadlines silently compute a day early
    or a day late.
    """

    __tablename__ = "ehs_holidays"
    __table_args__ = (UniqueConstraint("holiday_date", name="uq_ehs_holiday_date"),)

    id: Mapped[uuid.UUID] = mapped_column(
        sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
