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

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import hours as hours_service
from app.services.day_range import day_bounds, plant_today
from app.services.reporting import per_base_hours

ONTARIO = ZoneInfo("America/Toronto")



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
    """The five boxes along the top, and the rates and cost under them."""
    start, end = _year_bounds(year)

    row = (await db.execute(text("""
        select
          count(*)                                                  as incidents,
          count(*) filter (where injury_class is not null)          as injuries,
          count(*) filter (where injury_class = 'first_aid')        as first_aid,
          count(*) filter (where injury_class = 'medical_aid')      as medical,
          count(*) filter (where injury_class = 'lost_time')        as lost_time,
          count(*) filter (where injury_class in ('medical_aid','lost_time')
                              or mol_reportable)                    as recordable,
          count(*) filter (where injury_class in ('medical_aid','lost_time')) as dart,
          coalesce(sum(cost), 0)                                    as cost_total,
          count(*) filter (where cost is not null)                  as cost_known,
          coalesce(sum(lost_time_hours), 0)                         as lost_hours
        from ehs_incidents
       where occurred_at >= :start and occurred_at < :end
         and status <> 'draft'
    """), {"start": start, "end": end})).one()

    (incidents, injuries, first_aid, medical, lost_time, recordable, dart,
     cost_total, cost_known, lost_hours) = row

    # Days since the last injury, not since the last record of any kind.
    #
    # It counted everything — a near miss, a spill, a security matter — so
    # reporting a good catch reset the tile to zero, which punishes exactly the
    # behaviour the near-miss reporting is there to encourage. HSE asked for it
    # to mean medical incidents, and to be labelled that way. Same definition as
    # the count beside it: any injury class, drafts excluded, so an incident
    # re-classed as no injury stops counting in both. Days at the plant on both
    # ends; None when there has never been one, which the page says in words.
    last = (await db.execute(text(
        "select max(occurred_at) from ehs_incidents"
        " where injury_class is not null and status <> 'draft'"))).scalar()
    days_since = None
    if last is not None:
        days_since = (plant_today() - last.astimezone(ONTARIO).date()).days

    # An explicit figure wins — the reports let somebody try a what-if number —
    # but the stored monthly entries are the real source.
    entered = await hours_service.hours_for_year(db, year)
    hours = Decimal(str(hours_worked)) if hours_worked else entered.usable
    rir = per_base_hours(recordable, hours)

    return {
        "year": year,
        "incidents": incidents,
        "injuries": injuries,
        "first_aid": first_aid,
        "medical": medical,
        "lost_time": lost_time,
        "recordable": recordable,
        "rir": rir,
        # Moved here from Reports (round 9, "move the injury rate graph to the
        # dashboard near the top"), worked out by the same function.
        "lost_time_rate": per_base_hours(lost_time, hours),
        "dart_rate": per_base_hours(dart, hours),
        "days_since_last_incident": days_since,
        # Only what has been entered. `cost_known_count` of `incidents` carry a
        # cost at all; the page says so, so a partial sum is not read as the
        # whole cost. The formula that will fill this in is still with Kirk.
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
           and status <> 'draft'
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
           and status <> 'draft'
      group by 1, 2
    """), {"start": start, "end": end})).all()

    out = {f"{m:02d}": {"month": f"{m:02d}", "this_year": 0, "last_year": 0}
           for m in range(1, 13)}
    for y, m, n in rows:
        out[m]["this_year" if y == year else "last_year"] = n
    return list(out.values())


async def body_parts(db: AsyncSession, *, year: int) -> dict:
    """Which parts of the body were hurt: by entry, and by region of the figure.

    ★ Incidents, not injured people: `count(*)` over the persons table counted
    an incident twice when two people hurt the same part, and every other chart
    on this page counts incidents. It also has to agree with what opening the
    bar shows, or the reader counts the rows, gets a smaller number and stops
    believing both. The same holds for a region: an incident where one person
    hurt a hand and another a finger is one incident on the hand.

    `rows` keep the recorded wording, which is what the register opens on.
    `regions` shade the figure; `off_picture` are the counted rows the figure
    cannot show (body_map.py says why), so its total never silently falls
    short of the list's.
    """
    from app.services.body_map import region_for

    start, end = _year_bounds(year)
    found = (await db.execute(text("""
        select distinct i.id, p.body_part_label, v.code, v.attrs
          from ehs_incident_persons p
          join ehs_incidents i on i.id = p.incident_id
          left join ehs_vocabulary_items v on v.id = p.body_part_id
         where i.occurred_at >= :start and i.occurred_at < :end
           and p.body_part_label is not null
           and i.status <> 'draft'
    """), {"start": start, "end": end})).all()

    by_label: dict[str, set] = {}
    by_region: dict[str, set] = {}
    label_region: dict[str, str | None] = {}
    for incident_id, label, code, attrs in found:
        by_label.setdefault(label, set()).add(incident_id)
        region = region_for(code, attrs)
        # One wording, one place: the first entry seen decides, which only
        # matters if two entries share a label, and then they are one bar too.
        label_region.setdefault(label, region)
        if region:
            by_region.setdefault(region, set()).add(incident_id)

    rows = sorted(({"label": label, "count": len(ids), "region": label_region[label]}
                   for label, ids in by_label.items()),
                  key=lambda r: (-r["count"], r["label"]))
    return {
        "rows": rows,
        "regions": {k: len(v) for k, v in by_region.items()},
        "off_picture": [r for r in rows if r["region"] is None],
    }


async def injured_by_location(db: AsyncSession, *, year: int) -> list[dict]:
    """Where the injuries actually happened.

    ★ Round 4: "where it happened is part of the incident creation record, but
    that location is not accurate in the dashboard where people are getting hurt
    graph."

    It was not the location at all. The panel headed "Where people are getting
    hurt" was grouped by `department_id`, and an incident's department is the
    department of the person who was hurt — so somebody from Production injured
    in the warehouse counted towards Production, and the plant area the reporter
    chose on the form was not on the dashboard anywhere. Both are worth having;
    only one of them answers "where".

    Grouped on the live `locations` path rather than the snapshot on the
    incident, so renaming an area does not split its history into two bars.
    """
    start, end = _year_bounds(year)
    rows = (await db.execute(text("""
        select i.location_id, coalesce(l.path, 'Not recorded'), count(*)
          from ehs_incidents i
          left join locations l on l.id = i.location_id
         where i.occurred_at >= :start and i.occurred_at < :end
           and i.injury_class is not null
           and i.status <> 'draft'
      group by 1, 2
      order by count(*) desc, 2
    """), {"start": start, "end": end})).all()
    return [{"id": str(loc_id) if loc_id else None, "label": label, "count": n}
            for loc_id, label, n in rows]


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
           and i.status <> 'draft'
      group by 1, 2
      order by count(*) desc, 2
    """), {"start": start, "end": end})).all()
    return [{"id": str(dept_id) if dept_id else None, "label": label, "count": n}
            for dept_id, label, n in rows]


def _months(year: int) -> dict[str, dict]:
    return {f"{m:02d}": {"month": f"{m:02d}"} for m in range(1, 13)}


async def resolution_by_month(db: AsyncSession, *, year: int) -> list[dict]:
    """Average days to resolve, month by month (round 9 scorecard).

    Kirk's definition: "Total Number of Days for Resolution / Number of
    Incidents created within a month". Three choices, so the figure can be
    checked by hand:

    * The month is the one the incident happened in, at the plant — the date
      the register itself filters by, so opening a month shows these rows.
    * The days run from when the report was sent (`submitted_at`) to its
      closing. A reopened incident's `closed_at` is cleared on reopening and
      stamped again on the last close, so it is the last close that counts.
    * ★ The average is over the closed ones. One still open has no resolution
      time yet; counting it as zero days would make a month with a backlog
      look like a month that resolved everything instantly. How many are still
      open is reported beside it, under the register's own definition of open
      (any status but draft or closed).
    """
    start, end = _year_bounds(year)
    rows = (await db.execute(text("""
        select to_char(timezone('America/Toronto', occurred_at), 'MM') as m,
               count(*)                                   as reported,
               count(*) filter (where status = 'closed' and closed_at is not null) as closed,
               sum(extract(epoch from closed_at - coalesce(submitted_at, created_at)) / 86400.0)
                 filter (where status = 'closed' and closed_at is not null) as days,
               count(*) filter (where status <> 'closed')  as still_open
          from ehs_incidents
         where occurred_at >= :start and occurred_at < :end
           and status <> 'draft'
      group by 1
    """), {"start": start, "end": end})).all()
    out = {m: {**r, "reported": 0, "closed": 0, "average_days": None, "still_open": 0}
           for m, r in _months(year).items()}
    for m, reported, closed, days, still_open in rows:
        out[m].update(reported=reported, closed=closed, still_open=still_open,
                      average_days=(round(max(float(days), 0) / closed, 1) if closed else None))
    return list(out.values())


async def capa_by_month(db: AsyncSession, *, year: int) -> list[dict]:
    """Corrective actions raised, and closed, in each month.

    Closed means closed — verified where verification is asked for — not just
    marked done by the owner. A cancelled one was still raised.
    """
    start, end = _year_bounds(year)
    out = {m: {**r, "created": 0, "resolved": 0} for m, r in _months(year).items()}
    for col, key, extra in (("created_at", "created", ""),
                            ("closed_at", "resolved", "and status = 'closed'")):
        for m, n in (await db.execute(text(f"""
            select to_char(timezone('America/Toronto', {col}), 'MM'), count(*)
              from ehs_actions
             where {col} >= :start and {col} < :end {extra}
          group by 1
        """), {"start": start, "end": end})).all():
            out[m][key] = n
    return list(out.values())


async def assessments_by_month(db: AsyncSession, *, year: int) -> list[dict]:
    """Hazard assessments recorded each month — the same records the chart by
    supervisor counts.

    ★ Round 12, Kirk: "use the create date for reporting and graph purposes."
    A hazard assessment is a hazard recorded on the register, counted in the
    month it was saved. Until round 12 this counted risk scorings, incidents'
    included, by when they were scored."""
    start, end = _year_bounds(year)
    out = {m: {**r, "count": 0} for m, r in _months(year).items()}
    for m, n in (await db.execute(text("""
        select to_char(timezone('America/Toronto', created_at), 'MM'), count(*)
          from ehs_hazards
         where created_at >= :start and created_at < :end
      group by 1
    """), {"start": start, "end": end})).all():
        out[m]["count"] = n
    return list(out.values())


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    return day_bounds(date(year, month, 1), last)


async def supervisor_activity(db: AsyncSession, *, year: int, month: int) -> dict:
    """Near misses, hazard assessments and toolbox talks for one month, by
    supervisor (round 9; round 10 q10-supervisors).

    Whose column a record goes in:

    * a near miss — the reporter's supervisor, counted by when it was sent;
    * a hazard assessment — a hazard recorded on the register, in the column of
      the supervisor of whoever recorded it, counted by when it was saved;
    * a toolbox talk — the supervisor who held it;
    * ★ round 12 (the defaults Kirk accepted): a supervisor's own near misses
      and hazards go in their own column, not their manager's. Ken recorded a
      hazard in November and his column said 0: it was counted as a risk
      scoring, in the column of whoever supervises Ken.

    ★ "Supervisor" is `users.supervisor_id` as it is *now*. Somebody who moves
    team takes their earlier months with them; the chart answers "how is each
    team doing", read through today's reporting lines.

    ★ An anonymous near miss never goes in its reporter's supervisor's column,
    even though the reporter's id is kept: a column of one next to an anonymous
    report would say who wrote it. It goes with the records whose person has no
    supervisor, in the unassigned column. Drafts are not counted.
    """
    start, end = month_bounds(year, month)
    params = {"start": start, "end": end}
    cols: dict[object, dict] = {}

    def bucket(sup_id) -> dict:
        return cols.setdefault(sup_id, {
            "supervisor_id": str(sup_id) if sup_id else None,
            "near_miss": 0, "hazard_assessment": 0, "toolbox_talk": 0})

    from app.crud.toolbox import SUPERVISORY_ROLES
    from app.services.notify import by_roles
    params["sups"] = [u.id for u in await by_roles(db, SUPERVISORY_ROLES)]
    whose = """case when u.id = any(cast(:sups as uuid[])) then u.id
                     else u.supervisor_id end"""

    for sup, n in (await db.execute(text(f"""
        select case when i.is_anonymous then null else {whose} end, count(*)
          from ehs_incidents i
          left join users u on u.id = i.reported_by
         where i.form_kind = 'near_miss' and i.status <> 'draft'
           and coalesce(i.submitted_at, i.created_at) >= :start
           and coalesce(i.submitted_at, i.created_at) < :end
      group by 1
    """), params)).all():
        bucket(sup)["near_miss"] += n

    for sup, n in (await db.execute(text(f"""
        select {whose}, count(*)
          from ehs_hazards h
          left join users u on u.id = h.created_by
         where h.created_at >= :start and h.created_at < :end
      group by 1
    """), params)).all():
        bucket(sup)["hazard_assessment"] += n

    for sup, n in (await db.execute(text("""
        select supervisor_id, count(*)
          from ehs_tbt_deliveries
         where status = 'delivered' and held_at >= :start and held_at < :end
      group by 1
    """), params)).all():
        bucket(sup)["toolbox_talk"] += n

    ids = [k for k in cols if k is not None]
    names = {}
    if ids:
        names = dict((await db.execute(
            text("select id, full_name from users where id = any(:ids)"),
            {"ids": ids})).all())
    rows = [{**c, "name": names.get(k) or "Unknown person"} for k, c in cols.items() if k]
    rows.sort(key=lambda r: (-(r["near_miss"] + r["hazard_assessment"] + r["toolbox_talk"]),
                             r["name"]))
    unassigned = cols.get(None)
    return {
        "month": f"{year}-{month:02d}",
        "rows": rows,
        "unassigned": ({**unassigned, "name": "No supervisor set"} if unassigned else None),
    }


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
