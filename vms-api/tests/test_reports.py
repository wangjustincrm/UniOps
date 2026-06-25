"""Compliance report endpoints + service (W11+W12 S2-E / VMS-CR-001..002)."""
import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from tests.conftest import authed_client, make_token, make_user


# ── Helpers ─────────────────────────────────────────────────────────────────-

async def _make_visitor(client, *, name: str = "Cfia") -> str:
    resp = await client.post("/api/v1/visitors", json={
        "first_name": name,
        "last_name":  f"Test{uuid.uuid4().hex[:4]}",
        "company_name": f"{name}Co",
        "phone": "+1-555-0700",
        "visitor_type": "supplier",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _visit_payload(
    visitor_id: str, host_id: str, *,
    area: str = "office", arrival: datetime | None = None,
) -> dict:
    arrival = arrival or (datetime.now(timezone.utc) + timedelta(days=1))
    return {
        "visitor_id": visitor_id,
        "host_id": host_id,
        "visit_date": arrival.date().isoformat(),
        "planned_arrival": arrival.isoformat(),
        "planned_departure": (arrival + timedelta(hours=2)).isoformat(),
        "visit_purpose": "audit",
        "access_area": area,
    }


async def _force_status(visit_id: str, *, status: str, health: str | None = None) -> None:
    """Bypass approval / health-decl machinery for setup."""
    async with session_module.AsyncSessionLocal() as db:
        sets = ["status = :status"]
        params = {"id": uuid.UUID(visit_id), "status": status}
        if health is not None:
            sets.append("health_decl_status = :health")
            params["health"] = health
        await db.execute(
            text(f"UPDATE vms_visits SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        await db.commit()


# ── RBAC ────────────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_requester_cannot_export_cfia_log(requester):
    _, client = requester
    resp = await client.get(
        "/api/v1/reports/cfia-visit-log",
        params={"from": "2026-01-01", "to": "2026-12-31"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_requester_cannot_export_gmp_summary(requester):
    _, client = requester
    resp = await client.get(
        "/api/v1/reports/gmp-area-summary",
        params={"from": "2026-01-01", "to": "2026-12-31"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_auditor_can_export_cfia_log(test_engine):
    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get(
            "/api/v1/reports/cfia-visit-log",
            params={"from": "2026-01-01", "to": "2026-12-31"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    # Must have at least the header row.
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][0] == "date_of_visit"
    assert "on_site_minutes" in rows[0]


# ── CSV contents ────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_cfia_log_includes_confirmed_visits_in_window(test_engine, requester):
    user, client = requester
    visitor = await _make_visitor(client, name="CfiaInclude")
    arrival = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
    created = (await client.post(
        "/api/v1/visits",
        json=_visit_payload(visitor, str(user.id), area="laboratory", arrival=arrival),
    )).json()
    await _force_status(created["id"], status="confirmed", health="passed")

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get(
            "/api/v1/reports/cfia-visit-log",
            params={"from": "2026-04-01", "to": "2026-04-30"},
        )
    assert resp.status_code == 200, resp.text
    rows = list(csv.reader(io.StringIO(resp.text)))
    # Header + at least our row.
    assert len(rows) >= 2
    # Our visit should appear; visit date in iso form.
    dates = {r[0] for r in rows[1:]}
    assert "2026-04-10" in dates


@pytest.mark.asyncio
async def test_cfia_log_excludes_pending_approval(test_engine, requester):
    """Visits in pending_approval/cancelled never produced a regulator
    presence so they MUST NOT show up in the CFIA log."""
    user, client = requester
    visitor = await _make_visitor(client, name="CfiaSkip")
    arrival = datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc)
    created = (await client.post(
        "/api/v1/visits",
        json=_visit_payload(visitor, str(user.id), area="laboratory", arrival=arrival),
    )).json()
    # Force pending_approval.
    await _force_status(created["id"], status="pending_approval")

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get(
            "/api/v1/reports/cfia-visit-log",
            params={"from": "2026-04-15", "to": "2026-04-30"},
        )
    rows = list(csv.reader(io.StringIO(resp.text)))
    # No data rows for the 4/20 visit (header still present).
    for r in rows[1:]:
        # date_of_visit is column 0
        assert r[0] != "2026-04-20" or r[2] != "CfiaSkipCo"


@pytest.mark.asyncio
async def test_cfia_log_shows_department_name_not_id(test_engine, requester):
    """host_department must be the department NAME, not its UUID (VMS-CR-001)."""
    from app.models.department_mirror import Department

    user, client = requester
    dept_id = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(Department(id=dept_id, name="Quality Assurance"))
        await db.commit()
    host = await make_user(test_engine, role="requester", department_id=dept_id)

    visitor = await _make_visitor(client, name="DeptName")
    arrival = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)
    created = (await client.post(
        "/api/v1/visits",
        json=_visit_payload(visitor, str(host.id), area="office", arrival=arrival),
    )).json()
    await _force_status(created["id"], status="confirmed")

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get(
            "/api/v1/reports/cfia-visit-log",
            params={"from": "2026-07-01", "to": "2026-07-31"},
        )
    rows = list(csv.reader(io.StringIO(resp.text)))
    ours = [r for r in rows[1:] if r[0] == "2026-07-10" and r[2] == "DeptNameCo"]
    assert ours, resp.text
    # Column 5 is host_department — must be the name, not the UUID.
    assert ours[0][5] == "Quality Assurance"
    assert ours[0][5] != str(dept_id)


@pytest.mark.asyncio
async def test_cfia_log_times_are_local_not_utc(test_engine, requester):
    """Times must render in the plant's local timezone (offset), not UTC 'Z'."""
    user, client = requester
    visitor = await _make_visitor(client, name="CfiaTz")
    # 13:00 UTC on a summer date → 09:00-04:00 Eastern (EDT).
    arrival = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)
    created = (await client.post(
        "/api/v1/visits",
        json=_visit_payload(visitor, str(user.id), area="office", arrival=arrival),
    )).json()
    await _force_status(created["id"], status="confirmed")

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get(
            "/api/v1/reports/cfia-visit-log",
            params={"from": "2026-07-01", "to": "2026-07-31"},
        )
    rows = list(csv.reader(io.StringIO(resp.text)))
    pa = rows[0].index("planned_arrival")
    ours = [r for r in rows[1:] if r[2] == "CfiaTzCo"]
    assert ours, resp.text
    val = ours[0][pa]
    # Local Eastern offset, not UTC.
    assert not val.endswith("Z")
    assert "+00:00" not in val
    assert ("-04:00" in val or "-05:00" in val)
    assert "T09:00:00" in val   # 13:00 UTC == 09:00 EDT


@pytest.mark.asyncio
async def test_gmp_summary_returns_one_row_per_area(test_engine):
    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get(
            "/api/v1/reports/gmp-area-summary",
            params={"from": "2026-01-01", "to": "2026-12-31"},
        )
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    # Header + production_gmp + laboratory = 3 rows.
    assert len(rows) == 3
    assert rows[0][0] == "access_area"
    areas = {r[0] for r in rows[1:]}
    assert areas == {"production_gmp", "laboratory"}


@pytest.mark.asyncio
async def test_gmp_summary_counts_passed_failed(test_engine, requester):
    user, client = requester
    visitor = await _make_visitor(client, name="GmpCount")
    arrival = datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc)
    v_pass = (await client.post(
        "/api/v1/visits",
        json=_visit_payload(visitor, str(user.id), area="production_gmp", arrival=arrival),
    )).json()
    await _force_status(v_pass["id"], status="checked_out", health="passed")

    v_fail = (await client.post(
        "/api/v1/visits",
        json=_visit_payload(
            visitor, str(user.id), area="production_gmp",
            arrival=arrival + timedelta(hours=1),
        ),
    )).json()
    await _force_status(v_fail["id"], status="checked_out", health="failed")

    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get(
            "/api/v1/reports/gmp-area-summary",
            params={"from": "2026-05-01", "to": "2026-05-31"},
        )
    rows = list(csv.reader(io.StringIO(resp.text)))
    gmp = next(r for r in rows[1:] if r[0] == "production_gmp")
    # columns: area,total,passed,failed,restricted,not_required,no_decl,after_hours,unreturned
    assert int(gmp[2]) >= 1   # passed
    assert int(gmp[3]) >= 1   # failed


# ── Compliance metrics ──────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_dashboard_compliance_open_to_any_user(requester):
    _, client = requester
    resp = await client.get("/api/v1/dashboard/compliance")
    assert resp.status_code == 200
    body = resp.json()
    assert "gmp_visits_this_month" in body
    assert "gmp_pass_rate" in body
    assert "unreturned_badges" in body
    assert "after_hours_visits_today" in body
    assert "as_of" in body


@pytest.mark.asyncio
async def test_compliance_pass_rate_none_when_no_outcomes(test_engine):
    """No GMP visits → pass_rate must be None, not 0.0 (avoids misleading 0%)."""
    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get("/api/v1/dashboard/compliance")
    body = resp.json()
    # Other tests may have created GMP visits this month — only assert the
    # shape and the "no outcomes" case via direct service call below.

    # Direct service check: build a window with definitely no visits.
    from app.services.reports import compliance_metrics
    async with session_module.AsyncSessionLocal() as db:
        m = await compliance_metrics(
            db, today=date(1999, 1, 15), now=datetime(1999, 1, 15, tzinfo=timezone.utc),
        )
    assert m["gmp_visits_this_month"] == 0
    assert m["gmp_pass_rate"] is None


@pytest.mark.asyncio
async def test_audit_log_records_report_export(test_engine):
    """Every CSV export must leave an audit trail."""
    from app.models.audit_log import AuditLog
    from sqlalchemy import select as _select

    auditor = await make_user(test_engine, role="auditor", full_name="Audrey Auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get(
            "/api/v1/reports/cfia-visit-log",
            params={"from": "2026-04-01", "to": "2026-04-30"},
        )
    assert resp.status_code == 200

    async with session_module.AsyncSessionLocal() as db:
        rows = (await db.execute(
            _select(AuditLog).where(
                AuditLog.action_type == "export_cfia_visit_log",
                AuditLog.user_id == auditor.id,
            )
        )).scalars().all()
    assert len(rows) >= 1
    last = rows[-1]
    assert last.new_value["from"] == "2026-04-01"
    assert last.new_value["to"] == "2026-04-30"
