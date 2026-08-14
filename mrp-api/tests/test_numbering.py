"""Tests for `app/services/numbering.py`'s `next_timestamped_no` -- the
shared `{prefix}DDHHMM[-N]` generator behind `ForecastVersion.version_no`
and `MrpMpsRun.run_no` now that both replace their old random-hex/daily-
counter suffix with the creation timestamp (business owner's request, see
`.superpowers/sdd/2026-08-12-mrp-weekly-planning/task-13-report.md`).

Both columns carry `unique=True`, and `DDHHMM` alone only disambiguates to
the minute, so a same-minute collision is not hypothetical -- it has
already happened during manual and automated testing (see project memory
`project_uniops_document_number_collision` for the precedent this follows:
max-existing-tail + 1, serialized by a Postgres advisory lock). These tests
pin `now` explicitly via `next_timestamped_no`'s test-only override so they
never depend on which real wall-clock minute the suite happens to run in --
a test that relied on two calls landing in the same real minute would be
flaky exactly at minute boundaries, which is the one place it matters most.

Uses `ForecastVersion.version_no` as the backing unique column since it's a
real, already-`unique=True` column available via the `db_session` fixture;
nothing here is forecast-specific -- `MrpMpsRun.run_no` collisions are
handled by the exact same function (see `app/api/v1/mps.py::_next_run_no`).
"""
from datetime import datetime, timezone

import pytest

from app.models.forecast import ForecastVersion
from app.services.numbering import next_timestamped_no

# 2026-08-14 15:54:07 UTC -> DDHHMM = "141554". Picked arbitrarily; only the
# DD/HH/MM digits matter.
_NOW = datetime(2026, 8, 14, 15, 54, 7, tzinfo=timezone.utc)


async def _occupy(db_session, version_no: str) -> None:
    """Commit a row already holding `version_no`, standing in for a prior
    freeze -- this is the exact unique-constraint collision
    `next_timestamped_no` has to route around."""
    db_session.add(ForecastVersion(
        version_no=version_no, status="confirmed",
        horizon_start_month="2026-08", horizon_months=1,
    ))
    await db_session.commit()


@pytest.mark.anyio
async def test_no_collision_returns_the_bare_timestamp(db_session):
    """The common case: nothing else has claimed this minute yet.

    Mutation this catches: any change that unconditionally appends a
    disambiguator (e.g. always `-1`, or always including seconds) fails
    here -- the no-collision case must get exactly `DDHHMM`, matching what
    the owner asked for, not a suffixed variant."""
    no = await next_timestamped_no(
        db_session, lock_key=101, column=ForecastVersion.version_no,
        prefix="FCV-2026-08-", now=_NOW,
    )
    assert no == "FCV-2026-08-141554"


@pytest.mark.anyio
async def test_same_minute_collision_gets_a_distinct_suffixed_number(db_session):
    """The scenario named explicitly in the task: two creations for the same
    prefix inside the same UTC minute must both succeed with distinct
    numbers, not collide.

    Mutation this catches: dropping the existing-rows check (returning the
    bare timestamp unconditionally) would make `second == first` here --
    which is exactly the string pair that raises `IntegrityError` against
    the real `unique=True` column on insert."""
    first = await next_timestamped_no(
        db_session, lock_key=102, column=ForecastVersion.version_no,
        prefix="FCV-2026-08-", now=_NOW,
    )
    await _occupy(db_session, first)

    second = await next_timestamped_no(
        db_session, lock_key=102, column=ForecastVersion.version_no,
        prefix="FCV-2026-08-", now=_NOW,
    )
    assert second != first
    assert second == "FCV-2026-08-141554-2"


@pytest.mark.anyio
async def test_third_collision_uses_max_tail_plus_one_not_row_count(db_session):
    """Seeds `-2` and `-5` (a gap, out of sequence, and only 2 rows total)
    for the same minute, then asks for the next number.

    Mutation this catches: a "count existing rows + 1" implementation
    (instead of max-existing-tail + 1) would return `-3` here (2 rows -> 3rd)
    -- silently reusing a number if a `-3` or `-4` had ever existed and been
    deleted, or simply drifting from the actual max the moment a gap exists.
    Max-tail+1 must return `-6`, one past the highest suffix actually
    present."""
    base = await next_timestamped_no(
        db_session, lock_key=103, column=ForecastVersion.version_no,
        prefix="FCV-2026-08-", now=_NOW,
    )
    await _occupy(db_session, base)
    await _occupy(db_session, f"{base}-2")
    await _occupy(db_session, f"{base}-5")

    third = await next_timestamped_no(
        db_session, lock_key=103, column=ForecastVersion.version_no,
        prefix="FCV-2026-08-", now=_NOW,
    )
    assert third == f"{base}-6"


@pytest.mark.anyio
async def test_different_prefix_same_minute_does_not_collide(db_session):
    """A different anchor month occupying the identical DDHHMM must not be
    treated as a collision -- the prefix (which bakes in the anchor month)
    scopes the uniqueness check, not the timestamp alone.

    Mutation this catches: scoping the existing-numbers query by timestamp
    only (dropping the prefix from the `LIKE` filter) would make this
    unrelated row for a different month look like a collision and return
    `-2` here instead of the bare timestamp."""
    await _occupy(db_session, "FCV-2026-09-141554")

    no = await next_timestamped_no(
        db_session, lock_key=104, column=ForecastVersion.version_no,
        prefix="FCV-2026-08-", now=_NOW,
    )
    assert no == "FCV-2026-08-141554"
