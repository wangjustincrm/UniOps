"""Dashboard endpoints (PRD §6.5.5 / §2.5.2 VMS-AU-016).

`GET /dashboard/overview` returns high-level counters for the VMS home
page — on-site headcount, today's appointments, overdue visits. Counts
are unscoped (full plant state).

`GET /dashboard/compliance` returns the compliance KPIs that drive the
W11+W12 S2-E compliance card: GMP visits this month, GMP pass rate,
unreturned badges, after-hours arrivals today.
"""
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import visit as visit_crud
from app.services import reports as reports_svc

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardOverview(BaseModel):
    on_site_count: int
    today_count: int
    week_count: int
    overdue_count: int
    server_time: datetime


class ComplianceMetrics(BaseModel):
    gmp_visits_this_month: int
    # None when no health-decl outcomes exist yet this month — surfaces "n/a"
    # in the UI instead of a misleading 0%.
    gmp_pass_rate: float | None
    unreturned_badges: int
    after_hours_visits_today: int
    as_of: datetime


@router.get("/overview", response_model=DashboardOverview)
async def dashboard_overview(
    db: SessionDep,
    _: CurrentUserPayload,
):
    """High-level counters for the VMS home dashboard."""
    now = datetime.now(timezone.utc)
    today = now.date()
    return DashboardOverview(
        on_site_count=await visit_crud.count_active(db),
        today_count=await visit_crud.count_today(db, today),
        week_count=await visit_crud.count_this_week(db, today),
        overdue_count=await visit_crud.count_overdue(db, now=now),
        server_time=now,
    )


@router.get("/compliance", response_model=ComplianceMetrics)
async def dashboard_compliance(
    db: SessionDep,
    _: CurrentUserPayload,
):
    """KPIs for the dashboard compliance card.

    Unscoped — same rationale as `/overview`. Any authenticated user can
    see the plant-level numbers; the auditor-only gate sits on the raw
    reports under /reports/*.
    """
    now = datetime.now(timezone.utc)
    metrics = await reports_svc.compliance_metrics(db, today=now.date(), now=now)
    return ComplianceMetrics(**metrics)
