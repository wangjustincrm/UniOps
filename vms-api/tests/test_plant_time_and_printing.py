"""Plant-local time (D-07 / D-18), printable badge config (D-05), manual run lock (D-19)."""
import csv
import io
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import text

import app.db.session as session_module
from app.core.plant_time import plant_today
from app.services import notifications as notifications_svc
from app.services.scheduler import _ADVISORY_LOCK_KEY
from tests.conftest import authed_client, make_token, make_user
from tests.test_reports import _make_visitor


def test_plant_today_rolls_over_at_local_midnight_not_utc():
    # 2026-10-01 01:00 UTC is still 2026-09-30 21:00 in Toronto.
    assert plant_today(datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)) == date(2026, 9, 30)
    assert plant_today(datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)) == date(2026, 10, 1)


def test_email_times_are_local():
    fmt = notifications_svc._fmt_dt
    assert fmt(datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc)) == "2026-SEP-30 20:30 EDT"
    assert fmt(datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)) == "2026-JAN-15 09:00 EST"
    assert fmt(date(2026, 11, 11)) == "2026-NOV-11"
    assert fmt(None) == "—"


def test_registration_emails_show_plant_time_not_utc():
    """Training / PPE / PPE-prep mails printed planned_arrival as raw UTC
    isoformat ('2026-11-12T01:30:00+00:00'); now plant time, UI-DATE-002."""
    from types import SimpleNamespace
    visit = SimpleNamespace(
        id=uuid.uuid4(), visit_date=date(2026, 11, 11),
        planned_arrival=datetime(2026, 11, 12, 1, 30, tzinfo=timezone.utc),  # 20:30 EST on the 11th
        access_area="laboratory", access_areas=None,
    )
    visitor = SimpleNamespace(first_name="Ada", last_name="Lovelace", company_name="AE Co.")
    for build in (notifications_svc._format_training_email, notifications_svc._format_ppe_email):
        _subject, body = build(visit, visitor, None)
        assert "Visit date:     2026-NOV-11" in body
        assert "2026-NOV-11 20:30 EST" in body
        assert "+00:00" not in body


async def test_any_user_prints_with_the_admin_badge_config(requester, admin):
    _u, client = requester
    _a, admin_client = admin
    cfg = (await admin_client.get("/api/v1/admin/badge-config")).json()
    cfg["band"]["title"] = "GUEST"
    assert (await admin_client.put("/api/v1/admin/badge-config", json=cfg)).status_code == 200

    # The admin endpoint stays admin-only; printing reads /badge/config.
    assert (await client.get("/api/v1/admin/badge-config")).status_code == 403
    r = await client.get("/api/v1/badge/config")
    assert r.status_code == 200
    assert r.json()["band"]["title"] == "GUEST"
    cfg["band"]["title"] = "VISITOR"
    await admin_client.put("/api/v1/admin/badge-config", json=cfg)


async def test_after_hours_uses_plant_time(test_engine, requester):
    """22:00 UTC = 18:00 EDT (business hours); 23:00 UTC = 19:00 EDT (after hours).
    Reading the hour in UTC counted both as after-hours."""
    user, client = requester
    vid = await _make_visitor(client, name="Hours")
    day = date(2026, 6, 3)
    for hour_utc in (22, 23):
        arrival = datetime(2026, 6, 3, hour_utc, 0, tzinfo=timezone.utc)
        r = await client.post("/api/v1/visits", json={
            "visitor_id": vid, "host_id": str(user.id), "visit_date": day.isoformat(),
            "planned_arrival": arrival.isoformat(), "visit_purpose": "audit", "access_area": "laboratory",
        })
        assert r.status_code == 201, r.text
        async with session_module.AsyncSessionLocal() as db:
            await db.execute(text("UPDATE vms_visits SET status='confirmed' WHERE id=:i"),
                             {"i": uuid.UUID(r.json()["id"])})
            await db.commit()
    auditor = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(auditor.id, "auditor")) as c:
        resp = await c.get("/api/v1/reports/gmp-area-summary", params={"from": "2026-06-03", "to": "2026-06-03"})
    rows = {r[0]: r for r in csv.reader(io.StringIO(resp.text))}
    header = rows["access_area"]
    lab = dict(zip(header, rows["laboratory"]))
    assert lab["total_visits"] == "2"
    assert lab["after_hours_visits"] == "1"


async def test_manual_run_refused_while_a_run_holds_the_lock(admin):
    _a, admin_client = admin
    async with session_module.AsyncSessionLocal() as holder:
        assert (await holder.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})).scalar_one()
        try:
            r = await admin_client.post("/api/v1/admin/run-scheduled-jobs")
            assert r.status_code == 409, r.text
        finally:
            await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY})
            await holder.commit()
    assert (await admin_client.post("/api/v1/admin/run-scheduled-jobs")).status_code == 200
