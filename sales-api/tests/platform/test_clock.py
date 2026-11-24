"""The clock (≈ ABP's IClock, user 2026-11-24): one "now", one business day, the same zone in Python and in SQL —
at midnight, at both DST changes, for naive values — and a FixedClock that a job follows."""
import pathlib
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import Date, cast, func
from sqlalchemy.dialects import postgresql

from app.platform.clock import (
    FixedClock, SystemClock, business_date_sql, business_time_sql, clock, use_clock, utc_now,
)
from app.platform.options import ClockOptions, configure, options
from tests.platform.test_jobs import _job, _runs

UTC, TOR = timezone.utc, ZoneInfo("America/Toronto")

# (a UTC moment, its Toronto date and wall time): midnight in winter and in summer, and both DST changes of 2026.
EDGES = [
    (datetime(2026, 1, 15, 4, 59, 59, tzinfo=UTC), date(2026, 1, 14), time(23, 59, 59)),   # EST midnight = 05:00Z
    (datetime(2026, 1, 15, 5, 0, tzinfo=UTC), date(2026, 1, 15), time(0, 0)),
    (datetime(2026, 7, 15, 3, 59, 59, tzinfo=UTC), date(2026, 7, 14), time(23, 59, 59)),   # EDT midnight = 04:00Z
    (datetime(2026, 7, 15, 4, 0, tzinfo=UTC), date(2026, 7, 15), time(0, 0)),
    (datetime(2026, 3, 8, 6, 59, 59, tzinfo=UTC), date(2026, 3, 8), time(1, 59, 59)),      # spring: 02:00 → 03:00
    (datetime(2026, 3, 8, 7, 0, tzinfo=UTC), date(2026, 3, 8), time(3, 0)),
    (datetime(2026, 3, 9, 3, 59, 59, tzinfo=UTC), date(2026, 3, 8), time(23, 59, 59)),     # that evening: already EDT
    (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), date(2026, 11, 1), time(1, 30)),            # fall: 01:30 EDT …
    (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), date(2026, 11, 1), time(1, 30)),            # … and 01:30 EST
    (datetime(2026, 11, 2, 4, 59, 59, tzinfo=UTC), date(2026, 11, 1), time(23, 59, 59)),   # that evening: already EST
    (datetime(2026, 11, 2, 5, 0, tzinfo=UTC), date(2026, 11, 2), time(0, 0)),
]


@pytest.fixture(autouse=True)
def system_clock():
    before = options(ClockOptions)
    yield
    configure(before)


@pytest.mark.parametrize(("at", "day", "wall"), EDGES)
def test_the_business_day_at_midnight_and_at_both_dst_changes(at, day, wall):
    use_clock(FixedClock(at))
    local = clock().to_business(at)
    assert clock().today() == day and local.date() == day and local.time() == wall
    # what the replaced copies did, exactly (value, zone and text)
    old = at.astimezone(TOR)
    assert local == old and local.utcoffset() == old.utcoffset() and local.isoformat() == old.isoformat()


@pytest.mark.parametrize("day", [date(2026, 1, 15), date(2026, 3, 8), date(2026, 7, 15), date(2026, 11, 1)])
@pytest.mark.parametrize("wall", [time(0), time(12), time(23, 59, 59)])
def test_a_time_of_a_day_is_the_old_combine(day, wall):
    assert clock().at(day, wall).isoformat() == datetime.combine(day, wall, tzinfo=TOR).isoformat()


async def test_sql_and_python_give_the_same_business_day(uow_factory):
    from app.platform.actors import SYSTEM_ACTOR
    async with uow_factory(SYSTEM_ACTOR) as uow:
        for at, day, wall in EDGES:
            moment = sa.literal(at, sa.DateTime(timezone=True))
            got = (await uow.session.execute(sa.select(business_date_sql(moment), business_time_sql(moment)))).one()
            assert got[0] == day and got[1].time() == wall, at
        today = (await uow.session.execute(sa.select(business_date_sql()))).scalar_one()
    assert abs((today - SystemClock().today()).days) <= 1  # SQL's own now(): never frozen by a FixedClock


def test_the_sql_is_what_the_copies_compiled_to():
    text = lambda e: str(e.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # noqa: E731
    col = sa.column("at", sa.DateTime(timezone=True))
    assert text(business_date_sql()) == text(cast(func.timezone("America/Toronto", func.now()), Date))
    assert text(business_date_sql(col)) == text(cast(func.timezone("America/Toronto", col), Date))
    assert text(business_time_sql(col)) == text(func.timezone("America/Toronto", col))


def test_a_naive_value_follows_the_option_and_an_aware_one_is_converted():
    naive = datetime(2026, 7, 15, 12, 0)
    assert clock().normalize(naive) == datetime(2026, 7, 15, 12, 0, tzinfo=UTC)  # UTC by default
    aware = clock().normalize(datetime(2026, 7, 15, 12, 0, tzinfo=TOR))
    assert aware == datetime(2026, 7, 15, 16, 0, tzinfo=UTC) and aware.tzinfo is UTC
    configure(ClockOptions(naive_is="business"))
    assert clock().normalize(naive) == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        ClockOptions(naive_is="local")
    with pytest.raises(ValueError):
        ClockOptions(kind="local")
    with pytest.raises(ValueError):
        FixedClock(naive)


def test_the_zone_is_an_option_and_the_clock_a_strategy():
    configure(ClockOptions(business_timezone="Asia/Tokyo"))
    use_clock(FixedClock(datetime(2026, 7, 15, 16, 0, tzinfo=UTC)))  # keeps the zone
    assert clock().today() == date(2026, 7, 16) and utc_now() == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
    use_clock(None)
    assert isinstance(clock(), SystemClock) and utc_now().tzinfo is UTC
    assert abs(utc_now() - datetime.now(UTC)) < timedelta(seconds=5)


async def test_a_job_follows_a_fixed_clock(uow_factory):
    t = FixedClock(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    use_clock(t)
    job = _job(uow_factory)  # its default clock reads the configured one when called
    assert (await job.tick()) is not None
    [run] = await _runs(uow_factory)
    assert run.started_at == run.finished_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    t.advance(timedelta(minutes=4))
    assert (await job.tick()) is None       # every 5 minutes: not due
    t.advance(timedelta(minutes=1))
    assert (await job.tick()) is not None   # due


def test_no_copy_comes_back():
    """`now` and the business zone come from the clock only (one-off scripts aside)."""
    root = pathlib.Path(__file__).parents[2] / "app"
    bad = re.compile(r"datetime\.(now|utcnow|today)\(|date\.today\(|ZoneInfo\(\"|[\"']America/Toronto[\"']")
    allowed = {"platform/clock.py", "platform/options.py"}
    found = [f"{p.relative_to(root)}:{n}" for p in sorted(root.rglob("*.py")) for n, line in enumerate(p.read_text().splitlines(), 1)
             if bad.search(line) and str(p.relative_to(root)) not in allowed and "scripts" not in p.parts]
    assert found == []
