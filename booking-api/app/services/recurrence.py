"""Recurring series expansion logic.

Produces:
  SeriesSpec      — Pydantic model describing a recurrence rule.
  expand_series   — Expand a series into (starts_at, ends_at) occurrence tuples.
  build_rrule_string — Build an iCal-compatible RRULE string for storage.

Consumed by:
  - app/api/v1/bookings.py  (Task 7)
  - app/api/v1/precheck.py  (Task 7 wiring)
  - Tasks 8, 14, 15
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# SeriesSpec
# ─────────────────────────────────────────────────────────────────────────────

class SeriesSpec(BaseModel):
    """Recurring series parameters.

    Exactly one of ``count`` or ``until`` must be provided.
    ``interval`` must be >= 1 (default 1).
    ``freq`` is "daily" or "weekly".
    """
    freq: Literal["daily", "weekly"]
    interval: int = 1
    count: int | None = None
    until: date | None = None

    @field_validator("interval")
    @classmethod
    def _interval_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("interval must be >= 1")
        return v

    @model_validator(mode="after")
    def _exactly_one_of_count_or_until(self) -> "SeriesSpec":
        has_count = self.count is not None
        has_until = self.until is not None
        if has_count and has_until:
            raise ValueError("Provide exactly one of 'count' or 'until', not both")
        if not has_count and not has_until:
            raise ValueError("One of 'count' or 'until' is required")
        return self


# ─────────────────────────────────────────────────────────────────────────────
# expand_series
# ─────────────────────────────────────────────────────────────────────────────

def expand_series(
    starts_at: datetime,
    ends_at: datetime,
    spec: SeriesSpec,
    *,
    advance_days: int,
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    """Expand a series spec into a list of (starts_at, ends_at) occurrence tuples.

    Rules:
    - First occurrence = given starts_at / ends_at (preserved as-is).
    - ``weekly`` recurs on the same weekday as starts_at, every ``interval`` weeks.
    - ``daily`` recurs every ``interval`` days.
    - ``until`` is inclusive (local date in ``tz``): include occurrence if its
      local date <= until.
    - Occurrences beyond now + advance_days are silently dropped AFTER the
      count/until cap is applied.
    - Raises ValueError if the total candidate occurrences (before advance-window
      trimming) would exceed 60.

    Args:
        starts_at:    First occurrence start (timezone-aware).
        ends_at:      First occurrence end (timezone-aware).
        spec:         SeriesSpec describing freq/interval/count/until.
        advance_days: Maximum days into the future; occurrences beyond are dropped.
        tz:           Display timezone (used for date comparisons against until).

    Returns:
        List of (starts_at, ends_at) tuples, in chronological order.
    """
    duration = ends_at - starts_at
    now = datetime.now(tz)
    cutoff = now + timedelta(days=advance_days)

    step = (
        timedelta(weeks=spec.interval)
        if spec.freq == "weekly"
        else timedelta(days=spec.interval)
    )

    # First pass: generate all candidate occurrences (ignoring advance-window).
    # This is used to enforce the >60 guard BEFORE any advance-window trimming.
    candidates: list[tuple[datetime, datetime]] = []
    current_start = starts_at

    while True:
        # until: check local date (inclusive)
        if spec.until is not None:
            local_date = current_start.astimezone(tz).date()
            if local_date > spec.until:
                break

        # count: check iteration limit
        if spec.count is not None and len(candidates) >= spec.count:
            break

        candidates.append((current_start, current_start + duration))

        # Guard: >60 candidates → raise
        if len(candidates) > 60:
            raise ValueError(
                "Recurring series would generate more than 60 occurrences. "
                "Reduce count or shorten the until date."
            )

        current_start = current_start + step

    # Second pass: apply advance-window cap (silently drop beyond now+advance_days)
    occurrences = [
        (s, e) for s, e in candidates if s <= cutoff
    ]

    return occurrences


# ─────────────────────────────────────────────────────────────────────────────
# build_rrule_string
# ─────────────────────────────────────────────────────────────────────────────

def build_rrule_string(spec: SeriesSpec, until_fallback: date) -> str:
    """Build an iCal-compatible RRULE string for storage.

    Format: ``FREQ=...;INTERVAL=...`` + either ``;COUNT=n`` or ``;UNTIL=YYYYMMDDThhmmssZ``.

    Args:
        spec:           SeriesSpec.
        until_fallback: Fallback UNTIL date used when both spec.count and spec.until are None
                        (should not happen under normal validation).

    Returns:
        RRULE string, e.g. ``"FREQ=WEEKLY;INTERVAL=1;COUNT=4"``.
    """
    parts = [
        f"FREQ={spec.freq.upper()}",
        f"INTERVAL={spec.interval}",
    ]
    if spec.count is not None:
        parts.append(f"COUNT={spec.count}")
    elif spec.until is not None:
        parts.append(f"UNTIL={spec.until:%Y%m%dT235959Z}")
    else:
        # Fallback (should not happen under normal validator flow)
        parts.append(f"UNTIL={until_fallback:%Y%m%dT235959Z}")
    return ";".join(parts)
