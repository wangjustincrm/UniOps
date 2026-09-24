"""The management dashboard HSE circulates, reproduced from live data.

They shared the spreadsheet version they send the management team each month and
asked for it to be replicated. This builds the same tiles and the same charts
off the records the module already holds.

Two panels on their sheet have no source here and are reported as such rather
than filled with something plausible:

* **JHSC meeting attendance** comes from the committee module. It reports
  "no meetings recorded" rather than a flat line when the year is empty — an
  empty chart and a committee that never met look identical otherwise.
* **RIR** needs hours worked. HSE enters one figure a month under Settings;
  where months are missing the rate is still shown, with a note saying which,
  because a short divisor makes the rate read *higher* than it is and nobody
  can tell by looking. With nothing entered at all it is null rather than
  zero — a zero injury rate reads like a good year rather than a missing
  divisor.

Everything else is real. The year is the calendar year in Ontario local time —
a report filed at 20:00 on 31 December belongs to that year, not to the next one
it already is in UTC. ★ That is now true: `_year_bounds` converts through
`day_bounds`, where before it handed Postgres two bare dates and got the UTC
year back.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import hours as hours_service
from app.services.day_range import day_bounds

ONTARIO = ZoneInfo("America/Toronto")
OSHA_BASE_HOURS = 200_000

# What the plant's three "safety participation" activities map to. HSE's chart
# counts these per supervisor.
PARTICIPATION = ("toolbox_talk", "good_catch", "hazard_assessment")


def _year_bounds(year: int) -> tuple[datetime, datetime]:
    """The year, as the instants that bound it here.

    ★ It used to return two bare dates. Postgres compares a date against a
    timestamptz by reading the date in the session's timezone, and that session
    is UTC — so the year was the UTC year, and an injury at 8pm on 31 December
    was counted in the following one. The docstring at the top of this file
    claimed otherwise, which is how it survived: the comment described the
    intention and nobody checked it against the comparison.
    """
    return day_bounds(date(year, 1, 1), date(year, 12, 31))


async def headline(db: AsyncSession, *, year: int, hours_worked: float | None) -> dict:  # noqa: PLR0914
    """The tiles along the top of HSE's sheet."""
    start, end = _year_bounds(year)

    row = (await db.execute(text("""
        select
          count(*) filter (where injury_class is not null)          as injuries,
          count(*) filter (where injury_class = 'first_aid')        as first_aid,
          count(*) filter (where injury_class = 'medical_aid')      as medical,
          count(*) filter (where injury_class = 'lost_time')        as lost_time,
          count(*) filter (where injury_class in ('medical_aid','lost_time')
                              or mol_reportable)                    as recordable,
          coalesce(sum(cost), 0)                                    as cost_total,
          count(*) filter (where cost is not null)                  as cost_known,
          coalesce(sum(lost_time_hours), 0)                         as lost_hours
        from ehs_incidents
       where occurred_at >= :start and occurred_at < :end
    """), {"start": start, "end": end})).one()

    injuries, first_aid, medical, lost_time, recordable, cost_total, cost_known, lost_hours = row

    # Days since the last injury, not since the last record of any kind.
    #
    # It counted everything — a near miss, a spill, a security matter — so
    # reporting a good catch reset the tile to zero, which punishes exactly the
    # behaviour the near-miss reporting is there to encourage. HSE asked for it
    # to mean medical incidents, and to be labelled that way.
    last = (await db.execute(text(
        "select max(occurred_at) from ehs_incidents where injury_class is not null"))).scalar()
    days_since = None
    if last is not None:
        days_since = (datetime.now(timezone.utc).astimezone(ONTARIO).date()
                      - last.astimezone(ONTARIO).date()).days

    # An explicit figure wins — the reports let somebody try a what-if number —
    # but the stored monthly entries are the real source.
    entered = await hours_service.hours_for_year(db, year)
    hours = Decimal(str(hours_worked)) if hours_worked else entered.usable
    rir = (round(float(Decimal(recordable) * OSHA_BASE_HOURS / hours), 2)
           if hours and hours > 0 else None)

    return {
        "year": year,
        "injuries": injuries,
        "first_aid": first_aid,
        "medical": medical,
        "lost_time": lost_time,
        "recordable": recordable,
        "rir": rir,
        "days_since_last_incident": days_since,
        "cost_total": float(cost_total or 0),
        # Averaged over incidents that have a cost, not over all of them: a
        # blank cost is unknown, not zero, and averaging over blanks understates
        # every incident that was actually costed.
        "cost_average": (round(float(cost_total) / cost_known, 2) if cost_known else None),
        "cost_known_count": cost_known,
        "lost_time_hours": float(lost_hours or 0),
        "hours_worked": float(hours) if hours else None,
        # Says which months are missing when some are, rather than presenting a
        # rate computed on a short divisor as though it were complete.
        "rir_note": None if hours_worked else entered.note(),
    }


async def medical_events_by_month(db: AsyncSession, *, year: int) -> list[dict]:
    """Their stacked column: first aid / medical / lost time, month by month."""
    start, end = _year_bounds(year)
    rows = (await db.execute(text("""
        select to_char(timezone('America/Toronto', occurred_at), 'MM') as m,
               injury_class,
               count(*)
          from ehs_incidents
         where occurred_at >= :start and occurred_at < :end
           and injury_class is not null
      group by 1, 2
    """), {"start": start, "end": end})).all()

    # Every month present, including the empty ones — a gap in a bar chart reads
    # as "no data collected", a zero reads as "nothing happened".
    out = {f"{m:02d}": {"month": f"{m:02d}", "first_aid": 0, "medical_aid": 0, "lost_time": 0}
           for m in range(1, 13)}
    for m, cls, n in rows:
        if cls in out[m]:
            out[m][cls] = n
    return list(out.values())


async def near_misses_by_month(db: AsyncSession, *, year: int) -> list[dict]:
    """Near misses and good catches per month, this year against last.

    ★ Two years wide, and bounded the same way one year is. It used to pass two
    bare dates, which Postgres read in UTC while the months below are grouped in
    Ontario time — so the window and the grouping disagreed at both ends and the
    January and December bars were each short by whatever fell in the four hours
    between them.
    """
    start, end = day_bounds(date(year - 1, 1, 1), date(year, 12, 31))
    rows = (await db.execute(text("""
        select extract(year  from timezone('America/Toronto', occurred_at))::int as y,
               to_char(timezone('America/Toronto', occurred_at), 'MM')          as m,
               count(*)
          from ehs_incidents
         where form_kind = 'near_miss'
           and occurred_at >= :start and occurred_at < :end
      group by 1, 2
    """), {"start": start, "end": end})).all()

    out = {f"{m:02d}": {"month": f"{m:02d}", "this_year": 0, "last_year": 0}
           for m in range(1, 13)}
    for y, m, n in rows:
        out[m]["this_year" if y == year else "last_year"] = n
    return list(out.values())


async def body_parts(db: AsyncSession, *, year: int) -> list[dict]:
    """Which parts of the body were hurt — their table beside the figure.

    ★ Incidents, not injured people: `count(*)` over the persons table counted
    an incident twice when two people hurt the same part, and every other chart
    on this page counts incidents. It also has to agree with what opening the
    bar shows, or the reader counts the rows, gets a smaller number and stops
    believing both.
    """
    start, end = _year_bounds(year)
    rows = (await db.execute(text("""
        select p.body_part_label, count(distinct i.id)
          from ehs_incident_persons p
          join ehs_incidents i on i.id = p.incident_id
         where i.occurred_at >= :start and i.occurred_at < :end
           and p.body_part_label is not null
      group by 1
      order by count(distinct i.id) desc, 1
    """), {"start": start, "end": end})).all()
    return [{"label": label, "count": n} for label, n in rows]


async def injured_by_department(db: AsyncSession, *, year: int) -> list[dict]:
    """Their horizontal bars. Departments with no injuries are left out —
    the chart answers "where are people getting hurt", not "list every
    department"."""
    start, end = _year_bounds(year)
    # The id travels with the label so the bar can be opened onto the records
    # behind it. Grouped by id as well, because two departments may share a
    # name and a chart that merged them would open onto only one of them.
    rows = (await db.execute(text("""
        select i.department_id, coalesce(d.name, 'Not recorded'), count(*)
          from ehs_incidents i
          left join departments d on d.id = i.department_id
         where i.occurred_at >= :start and i.occurred_at < :end
           and i.injury_class is not null
      group by 1, 2
      order by count(*) desc, 2
    """), {"start": start, "end": end})).all()
    return [{"id": str(dept_id) if dept_id else None, "label": label, "count": n}
            for dept_id, label, n in rows]


async def participation(db: AsyncSession, *, year: int) -> list[dict]:
    """Safety activity per person: toolbox talks, good catches, risk assessments.

    HSE's chart is headed "Training Opportunities Provided vs Completed" and
    stacks three activities per supervisor. These are the three the system can
    actually count.
    """
    start, end = _year_bounds(year)
    people: dict[str, dict] = {}

    def bucket(name: str) -> dict:
        return people.setdefault(
            name, {"person": name, "toolbox_talk": 0, "good_catch": 0, "hazard_assessment": 0})

    for name, n in (await db.execute(text("""
        select coalesce(supervisor_name, 'Unassigned'), count(*)
          from ehs_tbt_deliveries
         where status = 'delivered' and created_at >= :start and created_at < :end
      group by 1
    """), {"start": start, "end": end})).all():
        bucket(name)["toolbox_talk"] = n

    for name, n in (await db.execute(text("""
        select coalesce(reported_by_name, 'Anonymous'), count(*)
          from ehs_incidents
         where form_kind = 'near_miss'
           and occurred_at >= :start and occurred_at < :end
      group by 1
    """), {"start": start, "end": end})).all():
        bucket(name)["good_catch"] = n

    for name, n in (await db.execute(text("""
        select coalesce(assessed_by_name, 'Unattributed'), count(*)
          from ehs_risk_assessments
         where assessed_at >= :start and assessed_at < :end
      group by 1
    """), {"start": start, "end": end})).all():
        bucket(name)["hazard_assessment"] = n

    rows = list(people.values())
    rows.sort(key=lambda r: -(r["toolbox_talk"] + r["good_catch"] + r["hazard_assessment"]))
    return rows[:12]


async def jhsc_attendance(db: AsyncSession, *, year: int) -> dict:
    """Attendance at each meeting, against the size of the committee.

    Their sheet plots attendance against total membership. `invited` is the
    attendance sheet's own length rather than today's roster size, so a meeting
    held before somebody joined is not scored against a committee they were not
    on yet.

    A year with no meetings reports that in words. Returning twelve zeroes
    would draw a committee that met and nobody came, which is a different and
    much more alarming statement.
    """
    from app.crud import jhsc as jhsc_crud

    months = await jhsc_crud.attendance_by_month(db, year=year)
    composition = await jhsc_crud.composition(db)
    if not months:
        return {
            "available": False,
            "reason": ("No committee meetings have been recorded for this year yet."
                       if composition["total"]
                       else "The committee roster has not been set up yet."),
            "months": [],
            "members": composition["total"],
        }
    return {
        "available": True,
        "reason": None,
        "months": months,
        "members": composition["total"],
    }
