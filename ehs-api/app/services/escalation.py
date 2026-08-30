"""When to chase something, and whom to chase.

Both ladders are pure functions of (record, now) so they can be tested against
fixed clocks. The scheduler that calls them does the database work; the
decision of whether something is due for escalation is decided here, once.

The corrective-action ladder is the HSE Manager's own schedule, confirmed
2026-08-28: a reminder three days before the due date, another on the day,
the supervisor at five days overdue, and the HSE Manager at ten. It stops
there — it deliberately does not climb to senior management.

The statutory ladder is ours, and it is tighter, because the consequence is
different: missing a corrective action's due date is a management problem,
while missing the Ministry's forty-eight hours is a contravention.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

# ── Statutory deadlines ─────────────────────────────────────────────────────

STATUTORY_WARN_HOURS = 24   # level 1
STATUTORY_URGENT_HOURS = 4  # level 2
# level 3 = past due


def statutory_level(due_at: datetime, now: datetime) -> int:
    """The escalation level a deadline has reached. 0 means nothing yet."""
    remaining = due_at - now
    if remaining <= timedelta(0):
        return 3
    if remaining <= timedelta(hours=STATUTORY_URGENT_HOURS):
        return 2
    if remaining <= timedelta(hours=STATUTORY_WARN_HOURS):
        return 1
    return 0


@dataclass(frozen=True)
class CapaLadder:
    """The thresholds, read from ehs_config so HSE can change them."""

    remind_before_days: int = 3
    supervisor_days: int = 5
    manager_days: int = 10


# What each level means, for the notification the scheduler raises.
CAPA_LEVEL_AUDIENCE = {
    1: ("owner", "Due soon"),
    2: ("owner+supervisor", "Overdue"),
    3: ("owner+supervisor+hse_manager", "Escalated"),
}


def capa_level(due_date: date, today: date, ladder: CapaLadder) -> int:
    """The escalation level a corrective action has reached.

    Level 1 covers both the advance reminder and the due date itself: they go
    to the same person and saying it twice adds nothing. Levels 2 and 3 widen
    the audience, which is the part that matters.
    """
    days_late = (today - due_date).days
    if days_late >= ladder.manager_days:
        return 3
    if days_late >= ladder.supervisor_days:
        return 2
    if days_late >= -ladder.remind_before_days:
        return 1
    return 0
