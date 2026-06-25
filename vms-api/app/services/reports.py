"""Compliance report builders (W11+W12 S2-E, F8 'interim format').

Ships CSV-only by design — the user accepted the interim format and Excel
opens CSV cleanly. Adding openpyxl just for "looks like .xlsx" would be a
dependency for no real win until CFIA hands us the final mandated layout.

Each builder is an async generator yielding CSV rows as `str` (already
newline-terminated). The route streams them via `StreamingResponse` so we
never materialize the whole report in memory for plant-scale exports.

Both reports do their joins in raw-ish SQLAlchemy `select()` so we can
order/filter without pulling every row into Python.
"""
from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.department_mirror import Department
from app.models.user_mirror import User
from app.models.visit import AccessArea, HealthDeclStatus, Visit, VisitStatus
from app.models.visitor import Visitor


# ── CSV streaming helper ────────────────────────────────────────────────────-


def _csv_row(values: list) -> str:
    """Render one CSV row honoring quoting / escaping rules."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["" if v is None else str(v) for v in values])
    return buf.getvalue()


# Local timezone for the human-facing export. Stored timestamps are UTC; the
# CFIA log renders them in the plant's local time (settings.REPORT_TIMEZONE).
# Fall back to UTC rather than crash the API if the zone can't be resolved
# (bad config / missing tz database).
try:
    _LOCAL_TZ: ZoneInfo | timezone = ZoneInfo(settings.REPORT_TIMEZONE)
except Exception:  # noqa: BLE001 — any zoneinfo failure degrades to UTC
    _LOCAL_TZ = timezone.utc


def _fmt_dt(value: datetime | None) -> str:
    """ISO-8601 (seconds) in the plant's local timezone, with offset. Empty if None."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_LOCAL_TZ).replace(microsecond=0).isoformat()


def _on_site_minutes(arrival: datetime | None, departure: datetime | None) -> str:
    if arrival is None or departure is None:
        return ""
    delta = departure - arrival
    return str(int(delta.total_seconds() // 60))


# ── CFIA visit log (16-column interim layout) ───────────────────────────────-

_CFIA_HEADER = [
    "date_of_visit",
    "visitor_full_name",
    "company",
    "visitor_type",
    "host_full_name",
    "host_department",
    "access_area",
    "health_decl_status",
    "planned_arrival",
    "actual_arrival",
    "actual_departure",
    "on_site_minutes",
    "safety_training_confirmed",
    "badge_returned",
    "ppe_issued",
    "notes",
]


async def stream_cfia_visit_log(
    db: AsyncSession, *, date_from: date, date_to: date,
) -> AsyncIterator[str]:
    """CFIA-style visit log (PRD §6.5.4 / VMS-CR-001).

    16 columns, **one row per (visit, visitor)** pair, scoped to
    [date_from, date_to] inclusive. Multi-visitor visits expand to N rows
    so the regulator's headcount matches the actual site presence.
    Ignores `pending_approval` and `cancelled` visits — those never
    produced a regulator-visible site presence.

    `host_department` is the host's department NAME, resolved via a read-only
    mirror of epms-api's `departments` table (LEFT JOIN so a host without a
    department, or an unresolved id, just yields an empty cell).
    """
    yield _csv_row(_CFIA_HEADER)

    q = (
        select(Visit, Visitor, User, Department.name)
        .join(Visitor, Visitor.id == Visit.visitor_id)
        .join(User, User.id == Visit.host_id)
        .outerjoin(Department, Department.id == User.department_id)
        .where(
            Visit.visit_date >= date_from,
            Visit.visit_date <= date_to,
            Visit.status.in_(
                [
                    VisitStatus.confirmed,
                    VisitStatus.checked_in,
                    VisitStatus.checked_out,
                    VisitStatus.no_show,
                ]
            ),
        )
        .order_by(Visit.visit_date.asc(), Visit.planned_arrival.asc())
    )

    result = await db.stream(q)
    async for visit, primary, host, dept_name in result:
        # Walk primary + companions in deterministic order so the same export
        # produces stable diffs across runs.
        visitors_to_emit: list[Visitor] = [primary]
        extras_ids = list(visit.additional_visitor_ids or [])
        if extras_ids:
            extras_rows = (
                await db.execute(select(Visitor).where(Visitor.id.in_(extras_ids)))
            ).scalars().all()
            extras_map = {v.id: v for v in extras_rows}
            for vid in extras_ids:
                try:
                    vobj = extras_map.get(uuid.UUID(str(vid)))
                except (ValueError, TypeError):
                    vobj = None
                if vobj is not None:
                    visitors_to_emit.append(vobj)

        ppe = visit.ppe_issued or {}
        ppe_summary = ", ".join(
            f"{k}={v}" for k, v in ppe.items() if k not in ("notes", "checkout_notes")
        )
        for v_row in visitors_to_emit:
            yield _csv_row(
                [
                    visit.visit_date.isoformat(),
                    f"{v_row.first_name} {v_row.last_name}".strip(),
                    v_row.company_name,
                    v_row.visitor_type.value,
                    host.full_name,
                    dept_name or "",
                    visit.access_area.value,
                    visit.health_decl_status.value if visit.health_decl_status else "",
                    _fmt_dt(visit.planned_arrival),
                    _fmt_dt(visit.actual_arrival),
                    _fmt_dt(visit.actual_departure),
                    _on_site_minutes(visit.actual_arrival, visit.actual_departure),
                    "yes" if visit.safety_training_confirmed else "no",
                    "yes" if visit.badge_returned else "no",
                    ppe_summary,
                    (visit.notes or "").replace("\n", " ").strip(),
                ]
            )


# ── GMP / Lab area summary ──────────────────────────────────────────────────-

_GMP_SUMMARY_HEADER = [
    "access_area",
    "total_visits",
    "passed",
    "failed",
    "restricted",
    "not_required",
    "no_health_decl",
    "after_hours_visits",
    "unreturned_badges",
]

# Plant operating hours — anything outside this window counts as "after hours".
_BUSINESS_HOUR_START = 7   # inclusive
_BUSINESS_HOUR_END = 19    # exclusive

_GMP_AREAS = (AccessArea.production_gmp, AccessArea.laboratory)


async def stream_gmp_area_summary(
    db: AsyncSession, *, date_from: date, date_to: date,
) -> AsyncIterator[str]:
    """One row per GMP/Lab access area for the requested window.

    Counters reflect VMS-CR-002 / §6.5.5: total visits, health-decl outcome
    distribution, after-hours count, unreturned badges (still a problem at
    checkout time, which the dashboard surfaces too).
    """
    yield _csv_row(_GMP_SUMMARY_HEADER)

    for area in _GMP_AREAS:
        base = (
            select(Visit)
            .where(
                Visit.access_area == area,
                Visit.visit_date >= date_from,
                Visit.visit_date <= date_to,
                Visit.status.in_(
                    [
                        VisitStatus.confirmed,
                        VisitStatus.checked_in,
                        VisitStatus.checked_out,
                        VisitStatus.no_show,
                    ]
                ),
            )
        )

        total = (
            await db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()

        # Outcome breakdown
        def _count_with(extra) -> int:
            q = base.with_only_columns(func.count(Visit.id)).where(extra)
            return q

        passed = (await db.execute(
            base.with_only_columns(func.count(Visit.id)).where(
                Visit.health_decl_status == HealthDeclStatus.passed
            )
        )).scalar_one()
        failed = (await db.execute(
            base.with_only_columns(func.count(Visit.id)).where(
                Visit.health_decl_status == HealthDeclStatus.failed
            )
        )).scalar_one()
        restricted = (await db.execute(
            base.with_only_columns(func.count(Visit.id)).where(
                Visit.health_decl_status == HealthDeclStatus.restricted
            )
        )).scalar_one()
        not_required = (await db.execute(
            base.with_only_columns(func.count(Visit.id)).where(
                Visit.health_decl_status == HealthDeclStatus.not_required
            )
        )).scalar_one()
        no_decl = (await db.execute(
            base.with_only_columns(func.count(Visit.id)).where(
                Visit.health_decl_status.is_(None)
            )
        )).scalar_one()

        after_hours = (await db.execute(
            base.with_only_columns(func.count(Visit.id)).where(
                (func.extract("hour", Visit.planned_arrival) < _BUSINESS_HOUR_START)
                | (func.extract("hour", Visit.planned_arrival) >= _BUSINESS_HOUR_END)
            )
        )).scalar_one()

        unreturned = (await db.execute(
            base.with_only_columns(func.count(Visit.id)).where(
                Visit.status == VisitStatus.checked_out,
                Visit.badge_returned.is_(False),
            )
        )).scalar_one()

        yield _csv_row(
            [
                area.value, total, passed, failed, restricted, not_required,
                no_decl, after_hours, unreturned,
            ]
        )


# ── Compliance metrics (dashboard) ──────────────────────────────────────────-


async def compliance_metrics(
    db: AsyncSession, *, today: date, now: datetime,
) -> dict:
    """Aggregated KPIs for the Dashboard compliance card (PRD §6.5.5).

    - gmp_visits_this_month: visits to GMP/Lab so far this calendar month
    - gmp_pass_rate: passed / (passed+failed+restricted) for the same window;
      `None` if denominator is 0 (avoids "0% pass rate" misleading display).
    - unreturned_badges: checked_out visits where badge_returned=False.
    - after_hours_visits_today: visits today with arrival outside business hours.
    """
    month_start = today.replace(day=1)

    gmp_base = (
        select(Visit)
        .where(
            Visit.access_area.in_(_GMP_AREAS),
            Visit.visit_date >= month_start,
            Visit.visit_date <= today,
            Visit.status.in_(
                [
                    VisitStatus.confirmed,
                    VisitStatus.checked_in,
                    VisitStatus.checked_out,
                    VisitStatus.no_show,
                ]
            ),
        )
    )

    gmp_total = (
        await db.execute(select(func.count()).select_from(gmp_base.subquery()))
    ).scalar_one()

    passed = (await db.execute(
        gmp_base.with_only_columns(func.count(Visit.id)).where(
            Visit.health_decl_status == HealthDeclStatus.passed
        )
    )).scalar_one()
    failed = (await db.execute(
        gmp_base.with_only_columns(func.count(Visit.id)).where(
            Visit.health_decl_status == HealthDeclStatus.failed
        )
    )).scalar_one()
    restricted = (await db.execute(
        gmp_base.with_only_columns(func.count(Visit.id)).where(
            Visit.health_decl_status == HealthDeclStatus.restricted
        )
    )).scalar_one()

    decided = passed + failed + restricted
    pass_rate = (passed / decided) if decided else None

    unreturned = (await db.execute(
        select(func.count(Visit.id)).where(
            Visit.status == VisitStatus.checked_out,
            Visit.badge_returned.is_(False),
        )
    )).scalar_one()

    after_hours_today = (await db.execute(
        select(func.count(Visit.id)).where(
            Visit.visit_date == today,
            Visit.status.in_(
                [
                    VisitStatus.confirmed,
                    VisitStatus.checked_in,
                    VisitStatus.checked_out,
                ]
            ),
            (func.extract("hour", Visit.planned_arrival) < _BUSINESS_HOUR_START)
            | (func.extract("hour", Visit.planned_arrival) >= _BUSINESS_HOUR_END),
        )
    )).scalar_one()

    return {
        "gmp_visits_this_month": int(gmp_total),
        "gmp_pass_rate": pass_rate,
        "unreturned_badges": int(unreturned),
        "after_hours_visits_today": int(after_hours_today),
        "as_of": now.astimezone(timezone.utc),
    }
