"""Seed dev/staging with realistic pilot data (VMS_SPRINT.md W8).

Generates the data needed to demo the S1 MVP closed loop:
  - 3 Host users (existing UniOps users — must already exist; this script
    looks them up by email and refuses to invent users)
  - 6 dummy visitors
  - 8 appointments for today across the access-area palette
  - 2 already checked-in (so the dashboard shows on-site headcount)
  - 1 deliberately overdue (planned_departure in the past)

Usage (from `c:/Project/uniops/vms-api/`):

    PYTHONPATH=. python -m scripts.seed_pilot

Optional env vars:
    HOSTS="alice@example.com,bob@example.com,carol@example.com"
                                  emails of existing UniOps users to use as Hosts

This is **strictly dev / staging**. It refuses to run if either:
  - the target DB is named "epms" (likely production), AND
  - $VMS_SEED_PROD is not set to "yes-im-sure".
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Make `app.*` importable when this script is run via `python -m scripts.seed_pilot`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings                # noqa: E402
from app.models.user_mirror import User             # noqa: E402
from app.models.visit import Visit, VisitStatus     # noqa: E402
from app.models.visitor import Visitor, VisitorType # noqa: E402


# ── Data ─────────────────────────────────────────────────────────────────────

_VISITOR_TEMPLATES = [
    {"first": "Alice",  "last": "Anderson",  "company": "AcmeCorp Equipment",   "type": "supplier"},
    {"first": "Bob",    "last": "Brennan",   "company": "Boreal Maintenance",   "type": "contractor"},
    {"first": "Carla",  "last": "Choudhary", "company": "CFIA",                  "type": "inspector"},
    {"first": "David",  "last": "Dubois",    "company": "Diligent Audit Group", "type": "auditor"},
    {"first": "Emma",   "last": "Edwards",   "company": "Eastern Cheese Co.",   "type": "customer"},
    {"first": "Frank",  "last": "Fontaine",  "company": "",                     "type": "interviewee"},
]

_APPOINTMENTS = [
    # (visitor_idx, area,                   status,                    minutes_before_now, mins_planned_duration)
    (0, "office",             "confirmed",  -30,  120),
    (1, "warehouse",          "checked_in", -90,  240),   # on-site for ~90 min
    (2, "production_gmp",     "confirmed",   60,  180),
    (3, "office",             "checked_in", -60,   45),   # OVERDUE (planned dur 45 min, on-site 60+)
    (4, "production_non_gmp", "confirmed",  120,  180),
    (5, "office",             "confirmed",  180,   60),
]


# ── Guards ──────────────────────────────────────────────────────────────────-

def _refuse_to_seed_prod() -> None:
    """Prevent accidental prod / shared-DB writes."""
    db_name = settings.POSTGRES_DB
    if db_name != "epms":
        return  # any non-prod DB name is fine
    if os.environ.get("VMS_SEED_PROD") == "yes-im-sure":
        return
    print(
        f"REFUSING to seed: POSTGRES_DB={db_name!r} looks like production.\n"
        f"  Set VMS_SEED_PROD=yes-im-sure to override (NOT for production).",
        file=sys.stderr,
    )
    sys.exit(2)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _resolve_hosts(db: AsyncSession, emails: list[str]) -> list[User]:
    """Look up existing UniOps users by email. Refuse to invent rows here —
    user creation is owned by epms-api."""
    users: list[User] = []
    for email in emails:
        u = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if u is None:
            print(
                f"ERROR: no user with email {email!r} exists.\n"
                f"  Create them via epms-api first (Admin → Users), "
                f"then re-run this script.",
                file=sys.stderr,
            )
            sys.exit(2)
        users.append(u)
    return users


async def _create_visitors(db: AsyncSession) -> list[Visitor]:
    rows: list[Visitor] = []
    for t in _VISITOR_TEMPLATES:
        v = Visitor(
            first_name=t["first"],
            last_name=t["last"] + "-" + uuid.uuid4().hex[:4],   # avoid collision on reruns
            company_name=t["company"] or "(individual)",
            phone="+1-555-" + str(1000 + len(rows)),
            email=f"{t['first'].lower()}-{uuid.uuid4().hex[:4]}@pilot.vms",
            visitor_type=VisitorType(t["type"]),
            id_verified=True,                                    # pre-verify so badge can print
        )
        db.add(v)
        rows.append(v)
    await db.flush()
    return rows


async def _create_appointments(
    db: AsyncSession,
    visitors: list[Visitor],
    hosts: list[User],
) -> list[Visit]:
    today = date.today()
    now = datetime.now(timezone.utc)
    rows: list[Visit] = []
    for i, (vidx, area, status, mins_offset, mins_duration) in enumerate(_APPOINTMENTS):
        host = hosts[i % len(hosts)]
        planned_arr = now + timedelta(minutes=mins_offset)
        planned_dep = planned_arr + timedelta(minutes=mins_duration)
        v = Visit(
            visitor_id=visitors[vidx].id,
            host_id=host.id,
            created_by=host.id,
            visit_date=today,
            planned_arrival=planned_arr,
            planned_departure=planned_dep,
            visit_purpose="meeting",
            access_area=area,
            status=VisitStatus(status),
            safety_training_confirmed=False,
            badge_returned=False,
            actual_arrival=planned_arr if status == "checked_in" else None,
        )
        db.add(v)
        rows.append(v)
    await db.flush()
    return rows


# ── Main ────────────────────────────────────────────────────────────────────-

async def main() -> None:
    _refuse_to_seed_prod()

    emails = os.environ.get("HOSTS", "").split(",")
    emails = [e.strip() for e in emails if e.strip()]
    if not emails:
        print(
            "ERROR: set HOSTS to a comma-separated list of existing UniOps user emails.\n"
            "       Example:  HOSTS=alice@example.com,bob@example.com python -m scripts.seed_pilot",
            file=sys.stderr,
        )
        sys.exit(2)

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        hosts = await _resolve_hosts(db, emails)
        visitors = await _create_visitors(db)
        appts = await _create_appointments(db, visitors, hosts)
        await db.commit()

    print(f"Seeded {len(visitors)} visitors and {len(appts)} visit appointments.")
    print(f"  Hosts: {', '.join(emails)}")
    print(f"  Today's appointments: {len(appts)}  "
          f"({sum(1 for a in appts if a.status == VisitStatus.checked_in)} checked in)")
    print(f"  Overdue: {sum(1 for a in appts if a.status == VisitStatus.checked_in and a.planned_departure < datetime.now(timezone.utc))}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
