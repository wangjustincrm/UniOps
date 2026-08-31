"""One reporting engine over every EHS record, rather than a report per screen.

HSE's requirement was explicit: "Build the Reporting tab around a common
reporting engine, rather than creating each report separately", with incidents,
hazards, observations, inspections, audits, CAPA, training and compliance all
grouped the same way.

What makes that possible is that every EHS table carries the same dimensions —
`occurred_at`, `location_id`, `department_id`, `shift_code`, `category_id`,
`severity`, `status`, `owner_id`, `closed_at`. That was locked in Phase 1
precisely so this could be one engine.

So a report is a triple: which entity, group by which dimension, over what
period. The SQL is generated from a whitelist rather than composed from user
input — every dimension and entity below maps to a column this code names, and
anything not in the table is rejected before a query is built.

Injury rates are here too. They are not a group-by: TRIR is a formula over a
count and a headcount-hours figure the system does not hold, so the hours are a
parameter and the arithmetic is written once, with the OSHA constant HSE
confirmed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# OSHA's constant: 100 employees × 50 weeks × 40 hours. HSE confirmed TRIR and
# RIR are the same metric under two names, so there is one figure, not two.
OSHA_BASE_HOURS = 200_000


@dataclass(frozen=True)
class Entity:
    key: str
    label: str
    table: str
    # The column that dates a record for period filtering.
    date_column: str
    # Dimensions this entity actually has. Not every table carries every one.
    dimensions: tuple[str, ...]


# Every entity here is one HSE listed. Adding a ninth is a row, not a rewrite.
ENTITIES: tuple[Entity, ...] = (
    Entity("incident", "Incidents", "ehs_incidents", "occurred_at",
           ("location", "department", "shift", "category", "injury_class",
            "status", "month")),
    Entity("action", "Corrective actions", "ehs_actions", "created_at",
           ("status", "priority", "owner", "month")),
    Entity("first_aid", "First aid", "ehs_first_aid_log", "occurred_at",
           ("location", "month")),
    Entity("inspection", "Inspections", "ehs_form_submissions", "occurred_at",
           ("location", "department", "status", "month")),
    Entity("risk", "Risk assessments", "ehs_risk_assessments", "assessed_at",
           ("rating", "month")),
    # Training has no status: a record exists because someone completed a
    # course. What varies is which course, and how it was delivered.
    Entity("training", "Training records", "ehs_training_records", "completed_on",
           ("course", "delivery", "month")),
    Entity("toolbox_talk", "Toolbox talks", "ehs_tbt_deliveries", "created_at",
           ("status", "department", "month")),
    Entity("equipment", "Equipment", "ehs_equipment", "created_at",
           ("location", "department", "status")),
)

BY_KEY = {e.key: e for e in ENTITIES}


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    # SQL fragment producing the group value, and the one producing its label.
    expression: str
    label_expression: str | None = None


DIMENSIONS: dict[str, Dimension] = {
    # Snapshot columns are grouped on directly: they are what the record said at
    # the time, which is the honest thing to report.
    "location": Dimension("location", "Area", "location_path"),
    "department": Dimension("department", "Department", "department_id::text",
                            "(select name from departments d where d.id = t.department_id)"),
    "shift": Dimension("shift", "Shift", "shift_code"),
    "category": Dimension("category", "Category", "category_label"),
    "injury_class": Dimension("injury_class", "Injury classification", "injury_class"),
    "status": Dimension("status", "Status", "status"),
    "priority": Dimension("priority", "Priority", "priority"),
    "rating": Dimension("rating", "Risk rating", "rating"),
    "owner": Dimension("owner", "Owner", "owner_name"),
    "course": Dimension("course", "Course", "course_label"),
    "delivery": Dimension("delivery", "Delivery", "delivery"),
    # Ontario-local, so a report for August does not put a 20:00 record on the
    # 31st into September.
    "month": Dimension("month", "Month",
                       "to_char(timezone('America/Toronto', {date_col}), 'YYYY-MM')"),
}


class ReportError(ValueError):
    """A report that cannot be built."""


async def group_by(
    db: AsyncSession, *, entity: str, dimension: str,
    date_from: date | None = None, date_to: date | None = None,
) -> list[dict]:
    """Count records of one entity, grouped by one dimension.

    Everything interpolated into the SQL comes from the tables above, never from
    the caller: `entity` and `dimension` are looked up and rejected if unknown,
    and the dates are bound parameters.
    """
    ent = BY_KEY.get(entity)
    if ent is None:
        raise ReportError(f"Unknown entity '{entity}'. One of: {', '.join(BY_KEY)}")
    if dimension not in ent.dimensions:
        raise ReportError(
            f"{ent.label} cannot be grouped by '{dimension}'. "
            f"One of: {', '.join(ent.dimensions)}")

    dim = DIMENSIONS[dimension]
    expr = dim.expression.format(date_col=ent.date_column)
    label_expr = (dim.label_expression or expr)

    where = ["1=1"]
    params: dict[str, object] = {}
    if date_from:
        where.append(f"t.{ent.date_column} >= :date_from")
        params["date_from"] = date_from
    if date_to:
        where.append(f"t.{ent.date_column} < :date_to")
        params["date_to"] = date_to

    sql = f"""
        select {expr} as value, {label_expr} as label, count(*) as count
          from {ent.table} t
         where {' and '.join(where)}
      group by {expr}, {label_expr}
      order by count(*) desc, 1
         limit 100
    """
    rows = (await db.execute(text(sql), params)).all()
    return [
        {"value": v, "label": lbl or (v if v is not None else "Not recorded"), "count": n}
        for v, lbl, n in rows
    ]


async def injury_rates(
    db: AsyncSession, *, hours_worked: Decimal | float | None,
    date_from: date | None = None, date_to: date | None = None,
) -> dict:
    """TRIR, LTIFR and DART over a period.

    `hours_worked` has no home in this system — it comes from payroll — so it is
    a parameter and the response says so when it is missing, rather than
    returning a zero that reads like a good month.
    """
    where = ["1=1"]
    params: dict[str, object] = {}
    if date_from:
        where.append("occurred_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        where.append("occurred_at < :date_to")
        params["date_to"] = date_to
    clause = " and ".join(where)

    row = (await db.execute(text(f"""
        select
          count(*) filter (where injury_class in ('medical_aid','lost_time')
                              or mol_reportable) as recordable,
          count(*) filter (where injury_class = 'lost_time')                as lost_time,
          count(*) filter (where injury_class in ('lost_time','medical_aid')) as dart
        from ehs_incidents
       where {clause}
    """), params)).one()

    recordable, lost_time, dart = row
    hours = Decimal(str(hours_worked)) if hours_worked else None

    def rate(n: int) -> float | None:
        if not hours or hours <= 0:
            return None
        return round(float(Decimal(n) * OSHA_BASE_HOURS / hours), 2)

    return {
        "recordable": recordable,
        "lost_time": lost_time,
        "dart_cases": dart,
        "hours_worked": float(hours) if hours else None,
        # Same metric, two names — HSE confirmed. Reported once.
        "trir": rate(recordable),
        "lost_time_rate": rate(lost_time),
        "dart_rate": rate(dart),
        "note": None if hours else (
            "Hours worked were not supplied, so the rates cannot be calculated. "
            "They come from payroll — record the monthly figure in Settings."
        ),
    }


async def compare_periods(
    db: AsyncSession, *, entity: str, dimension: str,
    period_a: tuple[date, date], period_b: tuple[date, date],
) -> list[dict]:
    """The same grouping over two periods, with the movement between them.

    This is E05: "hand injuries up 42% year on year, 68% of them in Production
    Area 104". The engine produces the numbers; which two periods to compare is
    the caller's business.
    """
    a = {r["value"]: r for r in await group_by(
        db, entity=entity, dimension=dimension, date_from=period_a[0], date_to=period_a[1])}
    b = {r["value"]: r for r in await group_by(
        db, entity=entity, dimension=dimension, date_from=period_b[0], date_to=period_b[1])}

    out = []
    for value in set(a) | set(b):
        count_a = a.get(value, {}).get("count", 0)
        count_b = b.get(value, {}).get("count", 0)
        out.append({
            "value": value,
            "label": (a.get(value) or b.get(value))["label"],
            "period_a": count_a,
            "period_b": count_b,
            "change": count_b - count_a,
            # None rather than infinity when something appears from nothing: a
            # percentage against zero is not a percentage.
            "change_percent": (
                round((count_b - count_a) * 100 / count_a) if count_a else None),
        })
    out.sort(key=lambda r: (-(r["period_b"]), r["label"] or ""))
    return out


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """[first, next first) — half-open, so no record lands in two months."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year + 1, 1, 1)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
