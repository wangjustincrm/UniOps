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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import hours as hours_service
from app.services import incident_type
from app.services.day_range import day_bounds, plant_today

# OSHA's constant: 100 employees × 50 weeks × 40 hours. HSE confirmed TRIR and
# RIR are the same metric under two names, so there is one figure, not two.
OSHA_BASE_HOURS = 200_000


def per_base_hours(n: int, hours: Decimal | float | None) -> float | None:
    """A count as a rate per 200,000 hours — the one place it is worked out,
    so the dashboard and Reports cannot disagree about the same year."""
    if not hours or hours <= 0:
        return None
    return round(float(Decimal(n) * OSHA_BASE_HOURS / Decimal(str(hours))), 2)


@dataclass(frozen=True)
class Entity:
    key: str
    label: str
    table: str
    # The column that dates a record for period filtering.
    date_column: str
    # Dimensions this entity actually has. Not every table carries every one.
    dimensions: tuple[str, ...]
    # Fixed narrowings, where the entity is a slice of a table rather than the
    # whole of it. Constants written here, never anything a caller sends — the
    # rule for this engine is that nothing from a request reaches the SQL as
    # text.
    #
    # ★ One column per condition: each is prefixed with the table alias
    # (`t.{cond}`), so a single string `a = 1 AND b = 2` would come out as
    # `t.a = 1 AND b = 2` with the second half unqualified. Two conditions are
    # two entries.
    where: tuple[str, ...] = ()
    # How one record is listed when somebody opens a bar: a reference, a line
    # saying what it is, and where it opens. SQL over the alias `t`, written
    # here like everything else. No `ref` means the entity is counted only.
    ref: str | None = None
    title: str | None = None
    path: str | None = None
    # What reading the records themselves needs, beyond the counts. The counts
    # are behind ehs.incident.read like the rest of the tab; a list of who did
    # which training is a training record and is held to that key.
    records_permission: str = "ehs.incident.read"


# Every entity here is one HSE listed. Adding a ninth is a row, not a rewrite.
ENTITIES: tuple[Entity, ...] = (
    # ★ Round 5 feedback (Ken Boucher, 1 Oct): "searching by type of incident,
    # toolbox talks etc." The type and the form it came in on are dimensions,
    # so they can be grouped on and narrowed to like anything else.
    Entity("incident", "Incidents", "ehs_incidents", "occurred_at",
           # Order matters: opening a bar regroups by the next one not yet
           # narrowed, so "where" follows "what kind" rather than the form,
           # which a type already decides.
           ("incident_type", "location", "department", "shift", "injury_class",
            "status", "category", "form_kind", "equipment", "month"),
           # ★ A draft is not an incident yet. Every form saves one (HSE's P0,
           # for the WiFi dead spots), so counting them inflates every bar, and
           # listing them shows the title of somebody's unsent report to anyone
           # who reads the register. Raised in review by uniops-41.
           where=("status <> 'draft'",),
           ref="t.incident_no", title="t.title", path="/incidents/{id}"),
    Entity("action", "Corrective actions", "ehs_actions", "created_at",
           ("status", "priority", "owner", "month"),
           ref="t.action_no", title="t.title", path="/actions/{id}",
           records_permission="ehs.action.read"),
    # ★ Round 5: the register was rewritten into incidents and its screen
    # removed, so "first aid" is a slice of the incident register now. Pointed at
    # the table that holds the records rather than left on the one they were
    # moved out of — a report that quietly returns nothing is worse than one
    # that is gone.
    Entity("first_aid", "First aid", "ehs_incidents", "occurred_at",
           ("location", "department", "shift", "status", "equipment", "month"),
           where=("injury_class = 'first_aid'", "status <> 'draft'"),
           ref="t.incident_no", title="t.title", path="/incidents/{id}"),
    # Drafts out for the same reason: 41 of 103 on dev were unsent forms.
    Entity("inspection", "Inspections", "ehs_form_submissions", "occurred_at",
           ("form", "location", "department", "status", "month"),
           where=("status <> 'draft'",),
           ref="t.submission_no",
           title="(select name from ehs_form_templates f where f.id = t.template_id)",
           path="/forms/submissions/{id}"),
    Entity("risk", "Risk assessments", "ehs_risk_assessments", "assessed_at",
           ("rating", "month")),
    # Training has no status: a record exists because someone completed a
    # course. What varies is which course, and how it was delivered.
    Entity("training", "Training records", "ehs_training_records", "completed_on",
           ("course", "delivery", "month"),
           ref="t.course_label", title="t.user_name", path=None,
           records_permission="ehs.training.read"),
    Entity("toolbox_talk", "Toolbox talks", "ehs_tbt_deliveries", "created_at",
           ("talk", "supervisor", "status", "department", "month"),
           ref="(select talk_no from ehs_toolbox_talks k where k.id = t.talk_id)",
           title="(select title from ehs_toolbox_talks k where k.id = t.talk_id)"
                 " || coalesce(' — ' || t.supervisor_name, '')",
           path="/toolbox-talks?delivery={id}"),
    # ★ No status column on this table — it carries is_active — so grouping it
    # by "status" was a 500 every time anybody picked it. Found by the test that
    # runs every entity against every grouping it offers.
    Entity("equipment", "Equipment", "ehs_equipment", "created_at",
           ("location", "department", "in_service"),
           ref="t.asset_no", title="t.name", path="/equipment"),
    # ★ Round 10 (open box): "Allow reports to be run against the equipment so
    # that we can track and report on the equipment that causes the most
    # injuries to staff" — incidents grouped by equipment, narrowed to an
    # injury class, opening the incidents behind each bar. The other records
    # that name equipment are here so the same question can be asked of them.
    # ★ Round 12, Kirk: "use the create date for reporting and graph purposes."
    Entity("hazard", "Hazards", "ehs_hazards", "created_at",
           ("equipment", "location", "department", "status", "month"),
           # ★ Review (uniops-8b): drafts are not counted or listed, as for
           # incidents — somebody's unsent record is theirs.
           where=("status <> 'draft'",),
           ref="t.hazard_no", title="t.title", path="/hazards/{id}",
           records_permission="ehs.action.read"),
    Entity("jsa", "Job safety analyses", "ehs_jsa", "created_at",
           ("equipment", "location", "department", "status", "month"),
           where=("status <> 'draft'",),
           ref="t.jsa_no", title="t.title", path="/jsa/{id}"),
    Entity("permit", "Work permits", "ehs_permits", "created_at",
           ("equipment", "location", "department", "permit_type", "status", "month"),
           where=("status <> 'draft'",),
           ref="t.permit_no", title="t.type_label || ' — ' || t.work_description",
           path="/permits/{id}", records_permission="ehs.permit.read"),
)

BY_KEY = {e.key: e for e in ENTITIES}


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    # SQL fragment producing the group value, and the one producing its label.
    expression: str
    label_expression: str | None = None
    # Labels for codes, where the names live in code rather than in a table.
    labels: dict | None = None


def _short(label: str) -> str:
    """"First Aid Injury — managed using…" reads as "First Aid Injury" in a list."""
    return label.split(" — ")[0]


def _type_labels() -> dict[str, str]:
    """Each incident type's short name, unless two would read the same.

    ★ UAT 14, TC-10: "near miss is on the list twice." "Near Miss — no injury
    but…" and "Near Miss — Food Safety" both shortened to "Near Miss".
    """
    # BY_CODE answers get/[]/in/iter, not items() — it is a view, not a store.
    full = {code: incident_type.BY_CODE[code].label for code in incident_type.BY_CODE}
    short = {code: _short(label) for code, label in full.items()}
    seen: dict[str, int] = {}
    for label in short.values():
        seen[label] = seen.get(label, 0) + 1

    def name(code: str) -> str:
        if seen[short[code]] == 1:
            return short[code]
        # Of two alike, the one whose tail only describes it ("— no injury but
        # has the potential…") keeps the short name; the one whose tail names
        # something ("— Food Safety") keeps it.
        tail = full[code].partition(" — ")[2]
        return short[code] if tail[:1].islower() else full[code]
    return {code: name(code) for code in full}


DIMENSIONS: dict[str, Dimension] = {
    # Snapshot columns are grouped on directly: they are what the record said at
    # the time, which is the honest thing to report.
    "location": Dimension("location", "Area", "location_path"),
    "department": Dimension("department", "Department", "department_id::text",
                            "(select name from departments d where d.id = t.department_id)"),
    "shift": Dimension("shift", "Shift", "shift_code"),
    # ★ UAT 13: "there are 120 incidents that are not categorized which is
    # wrong. Every type of incident has a category related to on the Incident
    # type registry." Only five built-in types carry a category of their own;
    # the rest — first aid, near miss, property damage… — had none, so most
    # incidents read "Not recorded". An incident is in its type's category
    # where it has one (Food Safety, Environmental…), and otherwise its type
    # is its category, as Settings → Incident types lists them.
    "category": Dimension("category", "Category",
                          "coalesce(category_label, extra->>'incident_type')",
                          labels=incident_type.BY_CODE),
    # ★ UAT 14, TC-10: "There are 75 'not recorded' injury classifications …
    # why is INC-2026-0137 labeled … not recorded, when it is a food safety
    # near miss." Blank is how the form stores "nobody was hurt": a near miss
    # is never asked (round 12, Q4), and on a damaged machine or a report-only
    # case the question is left blank when nobody was. So an incident with a
    # type and no class reads "No injury"; only one with no type at all — which
    # nothing told us about — is "Not recorded". And the classes read as HSE
    # names them, not as codes.
    "injury_class": Dimension(
        "injury_class", "Injury classification",
        "coalesce(injury_class, case when extra->>'incident_type' is not null then 'none' end)",
        labels={"first_aid": "First Aid", "medical_aid": "Medical Aid",
                "lost_time": "Lost Time", "none": "No injury"}),
    # The twelve types on HSE's form. Held in `extra`, which is where the form
    # writes it (crud/incident.py), so this reads it from there.
    "incident_type": Dimension(
        "incident_type", "Incident type", "(extra->>'incident_type')",
        # A live view: HSE's own types (GEN-01) are loaded per request.
        labels=incident_type.BY_CODE),
    "form_kind": Dimension(
        "form_kind", "Form", "form_kind",
        labels={"medical": "Injury or illness", "equipment": "Equipment",
                "near_miss": "Near miss or good catch"}),
    "form": Dimension("form", "Form", "template_id::text",
                      "(select name from ehs_form_templates f where f.id = t.template_id)"),
    "talk": Dimension("talk", "Talk", "talk_id::text",
                      "(select title from ehs_toolbox_talks k where k.id = t.talk_id)"),
    "supervisor": Dimension("supervisor", "Supervisor", "supervisor_name"),
    # Round 10: the equipment register. Named by its asset number and name as
    # the register holds them now; none is "Not recorded" like any other gap.
    "equipment": Dimension(
        "equipment", "Equipment", "equipment_id::text",
        "(select coalesce(e.asset_no || ' — ', '') || e.name"
        " from ehs_equipment e where e.id = t.equipment_id)"),
    "permit_type": Dimension("permit_type", "Kind of permit", "type_label"),
    "status": Dimension("status", "Status", "status"),
    "in_service": Dimension(
        "in_service", "In service", "is_active::text",
        labels={"true": "In service", "false": "Retired"}),
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


def _entity(entity: str) -> Entity:
    ent = BY_KEY.get(entity)
    if ent is None:
        raise ReportError(f"Unknown entity '{entity}'. One of: {', '.join(BY_KEY)}")
    return ent


def _where(
    ent: Entity, *, date_from: date | None, date_to: date | None,
    filters: dict[str, str | None] | None,
) -> tuple[str, dict[str, object]]:
    """The WHERE clause for one entity, period and set of filters.

    ★ The filter *values* are bound parameters; only the dimension names choose
    SQL, and those are looked up in the tables above and refused if unknown. A
    value of None means "not recorded" — the bar the engine labels that way.
    """
    where = ["1=1"]
    params: dict[str, object] = {}
    # The entity's own narrowing, where it is a slice of a table rather than
    # the whole of it. A constant from the table above, never anything a
    # caller sent — nothing from a request reaches this SQL as text.
    where.extend(f"t.{cond}" for cond in ent.where)
    if date_from:
        where.append(f"t.{ent.date_column} >= :date_from")
        params["date_from"] = date_from
    if date_to:
        where.append(f"t.{ent.date_column} < :date_to")
        params["date_to"] = date_to
    for i, (key, value) in enumerate((filters or {}).items()):
        if key not in ent.dimensions:
            raise ReportError(
                f"{ent.label} cannot be narrowed by '{key}'. "
                f"One of: {', '.join(ent.dimensions)}")
        expr = DIMENSIONS[key].expression.format(date_col=f"t.{ent.date_column}")
        if value is None:
            where.append(f"{expr} is null")
        else:
            where.append(f"{expr} = :f{i}")
            params[f"f{i}"] = value
    return " and ".join(where), params


async def group_by(
    db: AsyncSession, *, entity: str, dimension: str,
    date_from: date | None = None, date_to: date | None = None,
    filters: dict[str, str | None] | None = None,
) -> list[dict]:
    """Count records of one entity, grouped by one dimension.

    Everything interpolated into the SQL comes from the tables above, never from
    the caller: `entity` and `dimension` are looked up and rejected if unknown,
    and the dates and filter values are bound parameters.
    """
    ent = _entity(entity)
    if dimension not in ent.dimensions:
        raise ReportError(
            f"{ent.label} cannot be grouped by '{dimension}'. "
            f"One of: {', '.join(ent.dimensions)}")

    dim = DIMENSIONS[dimension]
    expr = dim.expression.format(date_col=ent.date_column)
    label_expr = (dim.label_expression or expr)
    clause, params = _where(ent, date_from=date_from, date_to=date_to, filters=filters)

    sql = f"""
        select {expr} as value, {label_expr} as label, count(*) as count
          from {ent.table} t
         where {clause}
      group by {expr}, {label_expr}
      order by count(*) desc, 1
         limit 100
    """
    rows = (await db.execute(text(sql), params)).all()
    labels = dim.labels or {}
    if labels is incident_type.BY_CODE:
        labels = _type_labels()
    return [
        {"value": v,
         "label": labels.get(v) or lbl or (v if v is not None else "Not recorded"),
         "count": n}
        for v, lbl, n in rows
    ]


RECORDS_LIMIT = 200


async def records(
    db: AsyncSession, *, entity: str,
    date_from: date | None = None, date_to: date | None = None,
    filters: dict[str, str | None] | None = None,
) -> dict:
    """The records behind a bar: what "searching" means on this tab.

    Round 5 feedback: a count says how many; the person asking wants to know
    which. Newest first, capped, and says so when it is capped rather than
    letting a list of two hundred read as all of them.
    """
    ent = _entity(entity)
    if ent.ref is None:
        raise ReportError(f"{ent.label} are counted here, not listed.")
    clause, params = _where(ent, date_from=date_from, date_to=date_to, filters=filters)
    total = (await db.execute(text(
        f"select count(*) from {ent.table} t where {clause}"), params)).scalar() or 0
    rows = (await db.execute(text(f"""
        select t.id, {ent.ref} as ref, {ent.title} as title, t.{ent.date_column} as at
          from {ent.table} t
         where {clause}
      order by t.{ent.date_column} desc nulls last
         limit {RECORDS_LIMIT}
    """), params)).all()
    return {
        "total": total,
        "rows": [
            {"id": str(i), "ref": ref, "title": title,
             "at": at.isoformat() if at is not None else None,
             "path": ent.path.format(id=i, ref=ref) if ent.path else None}
            for i, ref, title, at in rows
        ],
    }


async def injury_rates(
    db: AsyncSession, *, hours_worked: Decimal | float | None,
    date_from: date | None = None, date_to: date | None = None,
) -> dict:
    """TRIR, LTIFR and DART over a period.

    Hours come from the monthly figures HSE enters under Settings. The
    parameter remains, and wins when given, so somebody can try a what-if
    number without editing the record — but it is no longer the only source,
    which is what made these rates permanently blank.

    ★ The two windows agree to the month, not the day. Incidents are counted
    to the end of today; hours are by month, and the current month counts with
    whatever figure has been entered for it — a whole month entered on the
    15th makes the rate read low until the month is out.

    When months inside the period have no figure the rates are still returned,
    with a note naming them. A short divisor makes an injury rate read *higher*
    than it is, and there is nothing in the number itself to reveal that.
    """
    # ★ Drafts are not incidents yet; a saved, unsent report must not move TRIR.
    #
    # ★ One window for both halves of the rate. With no dates given the
    # incidents used to be counted over all time while the hours were this
    # year's, so every rate on Reports read high by however many earlier
    # years there were. And the dates were compared as bare dates against an
    # instant, which Postgres reads in UTC, so the window's edges sat four or
    # five hours off the plant's day. Both corrected in round 10, batch 4.
    today = plant_today()
    # `date_to` stays exclusive, as callers have always passed it.
    window_to = date_to or (today + timedelta(days=1))
    # With only an end, the year that end falls in — not this year, which for
    # an end last year would be an empty window reading as "nothing happened".
    window_from = date_from or date((window_to - timedelta(days=1)).year, 1, 1)
    start, _ = day_bounds(window_from, None)
    end, _ = day_bounds(window_to, None)

    row = (await db.execute(text("""
        select
          count(*) filter (where injury_class in ('medical_aid','lost_time')
                              or mol_reportable) as recordable,
          count(*) filter (where injury_class = 'lost_time')                as lost_time,
          count(*) filter (where injury_class in ('lost_time','medical_aid')) as dart
        from ehs_incidents
       where status <> 'draft' and occurred_at >= :start and occurred_at < :end
    """), {"start": start, "end": end})).one()

    recordable, lost_time, dart = row

    entered = None
    if hours_worked:
        hours = Decimal(str(hours_worked))
    else:
        entered = await hours_service.hours_for(
            db,
            date_from=window_from,
            date_to=date_to or date(today.year, today.month, 1) + timedelta(days=32),
        )
        hours = entered.usable

    def rate(n: int) -> float | None:
        return per_base_hours(n, hours)

    return {
        "recordable": recordable,
        "lost_time": lost_time,
        "dart_cases": dart,
        "hours_worked": float(hours) if hours else None,
        "hours_months_missing": entered.missing if entered else [],
        # Same metric, two names — HSE confirmed. Reported once.
        "trir": rate(recordable),
        "lost_time_rate": rate(lost_time),
        "dart_rate": rate(dart),
        # The service's own note where there is one (it names the missing
        # months); otherwise the plain "nothing entered" message.
        "note": (entered.note() if entered else None) or (
            None if hours else
            "Hours worked have not been entered, so the rates cannot be "
            "calculated. HSE records one figure a month under Settings."
        ),
    }


async def compare_periods(
    db: AsyncSession, *, entity: str, dimension: str,
    period_a: tuple[date, date], period_b: tuple[date, date],
    filters: dict[str, str | None] | None = None,
) -> list[dict]:
    """The same grouping over two periods, with the movement between them.

    This is E05: "hand injuries up 42% year on year, 68% of them in Production
    Area 104". The engine produces the numbers; which two periods to compare is
    the caller's business.
    """
    a = {r["value"]: r for r in await group_by(
        db, entity=entity, dimension=dimension, date_from=period_a[0], date_to=period_a[1],
        filters=filters)}
    b = {r["value"]: r for r in await group_by(
        db, entity=entity, dimension=dimension, date_from=period_b[0], date_to=period_b[1],
        filters=filters)}

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
