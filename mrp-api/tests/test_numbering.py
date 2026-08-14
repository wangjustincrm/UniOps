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
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

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


# ── Genuine concurrency (separate sessions/transactions, not one coroutine
# calling the function twice) ───────────────────────────────────────────────
#
# The four tests above call `next_timestamped_no` twice, sequentially,
# awaited one after the other in the SAME coroutine/session -- the first
# call's row is fully committed before the second call even starts. That
# exercises the max-tail scan (the disambiguation logic) but never actually
# contends for the advisory lock, because there is never a moment where two
# callers are both mid-flight. A code-review pass confirmed this by deleting
# the `pg_advisory_xact_lock` line from `numbering.py` and rerunning those
# four tests unchanged: all four still passed. The test below is the fix --
# see its docstring, and this file's "lock-removed verification" note below
# it, for what closes that gap.


@pytest.mark.anyio
async def test_concurrent_creates_for_the_same_prefix_serialize_and_get_distinct_numbers(db_engine):
    """Two GENUINELY concurrent callers -- separate `AsyncSession`s (hence
    separate Postgres transactions) contending for the identical
    `(lock_key, prefix)` -- must both succeed with distinct numbers. This is
    the lock's actual job, and the thing the four single-coroutine tests
    above cannot exercise (see the module-level note).

    Mechanics: caller A acquires the lock, computes its number, stages the
    insert, and then -- deliberately -- holds its transaction open
    (uncommitted) for a second before committing, simulating "another
    request is still in flight". Caller B starts 200ms later and tries to
    acquire the SAME lock while A still holds it. With the lock working,
    B's acquire attempt blocks at the Postgres level until A's transaction
    ends, so B's "what's already taken" read happens strictly AFTER A's row
    is committed and visible -- B correctly computes the `-2` suffix. A
    always starts first and B is delayed, so which caller gets which number
    is deterministic here; what is NOT predetermined, and is exactly what
    this test is checking, is whether B's read waits for A's write.

    This is verified to actually depend on the lock, not pass for
    unrelated reasons: with the `pg_advisory_xact_lock` call in
    `next_timestamped_no` temporarily deleted, this exact test raises
    `IntegrityError`. What actually happens without the lock: B's SELECT
    still reads "nothing taken" (A's insert is uncommitted, so still
    invisible under MVCC) and computes the SAME bare number as A -- but
    B's own INSERT then runs straight into Postgres' own unique-index
    enforcement, which blocks a second concurrent insert of an
    already-provisionally-taken key until the first transaction resolves.
    So B's INSERT blocks until A commits at ~1s, at which point B's
    now-unblocked insert discovers the row genuinely exists and raises
    `IntegrityError` on B's `commit()` -- not A's, as a simpler "whoever
    commits last loses" model would predict. Either way, something 500s
    instead of both requests succeeding with distinct numbers; see
    task-13-report.md for the real traceback this produced. Restored
    immediately after."""
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    fixed = datetime(2026, 8, 20, 11, 30, 0, tzinfo=timezone.utc)  # DDHHMM = 201130

    results: dict[str, str] = {}

    async def _caller_a() -> None:
        async with Session() as session:
            no = await next_timestamped_no(
                session, lock_key=201, column=ForecastVersion.version_no,
                prefix="FCV-2026-08-", now=fixed,
            )
            session.add(ForecastVersion(
                version_no=no, status="confirmed",
                horizon_start_month="2026-08", horizon_months=1,
            ))
            await session.flush()  # the row exists in A's txn, still invisible to B
            await asyncio.sleep(1.0)  # hold the lock open while B attempts below
            await session.commit()
        results["a"] = no

    async def _caller_b() -> None:
        await asyncio.sleep(0.2)  # let A acquire the lock and flush first
        async with Session() as session:
            no = await next_timestamped_no(
                session, lock_key=201, column=ForecastVersion.version_no,
                prefix="FCV-2026-08-", now=fixed,
            )
            session.add(ForecastVersion(
                version_no=no, status="confirmed",
                horizon_start_month="2026-08", horizon_months=1,
            ))
            await session.commit()
        results["b"] = no

    await asyncio.gather(_caller_a(), _caller_b())

    assert results["a"] == "FCV-2026-08-201130"
    assert results["b"] == "FCV-2026-08-201130-2"
    assert results["a"] != results["b"]
