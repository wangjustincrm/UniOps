"""Performance smoke (VMS_SPRINT.md W8).

Insert ~1000 visits, then time:
  - `GET /api/v1/visits?page_size=50`           list query
  - `GET /api/v1/visits/active`                 active-only filter
  - `GET /api/v1/dashboard/overview`            aggregate counters

Thresholds are deliberately generous — this test is a *regression detector*,
not a synthetic benchmark. It catches missing indexes, N+1 joins, or accidental
unfiltered scans. On a stock PG 15 + local Docker, all three endpoints come
back in low-tens of milliseconds; we assert "< 2 seconds" to leave plenty of
headroom for CI/Docker overhead.
"""
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.visit import Visit, VisitStatus
from app.models.visitor import Visitor, VisitorType


# Number of rows to seed. ~1000 is plenty to catch sequential scans without
# making the test painfully slow in CI.
_BULK_ROWS = 1000

# Wall-clock budget per endpoint (PRD §7.1: data query < 1s within 100k rows;
# we're at 1k rows so we should be far below).
_LATENCY_BUDGET_SECONDS = 2.0


@pytest.fixture
async def bulk_loaded(test_engine, requester):
    """Insert _BULK_ROWS visits owned by the `requester` user (plus one visitor).

    Function-scoped because the `requester` fixture is function-scoped. The
    insert is fast (bulk core insert, ~100ms on local PG) so the per-test
    overhead is acceptable.
    """
    user, client = requester

    # A single visitor row to keep the visit FKs valid.
    visitor_id = (await client.post("/api/v1/visitors", json={
        "first_name": "Bulk",
        "last_name":  f"Visitor-{uuid.uuid4().hex[:6]}",
        "company_name": "BulkCo",
        "phone": "+1-555-9999",
        "visitor_type": VisitorType.supplier.value,
    })).json()["id"]

    today = date.today()
    arrival = datetime.now(timezone.utc)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        # Bulk insert via core insert() — much faster than ORM .add() in a loop.
        rows = [
            {
                "id": uuid.uuid4(),
                "visitor_id": uuid.UUID(visitor_id),
                "host_id": user.id,
                "created_by": user.id,
                "visit_date": today - timedelta(days=i % 30),  # spread across last 30 days
                "planned_arrival": arrival - timedelta(days=i % 30),
                "visit_purpose": "meeting",
                "access_area": "office",
                "status": VisitStatus.confirmed.value,
                "safety_training_confirmed": False,
                "badge_returned": False,
            }
            for i in range(_BULK_ROWS)
        ]
        await db.execute(insert(Visit), rows)
        await db.commit()

    yield  # yield nothing — tests just rely on the seeded state existing


# ── Timing helpers ──────────────────────────────────────────────────────────-

async def _timed(coro):
    t0 = time.perf_counter()
    result = await coro
    return result, time.perf_counter() - t0


# ── Smokes ──────────────────────────────────────────────────────────────────-

@pytest.mark.asyncio
async def test_list_endpoint_under_budget(bulk_loaded, requester):
    """`GET /visits` with default pagination stays well under 2s after a 1k-row insert."""
    _, client = requester
    resp, elapsed = await _timed(client.get("/api/v1/visits?page_size=50"))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) <= 50
    assert body["total"] >= _BULK_ROWS
    assert elapsed < _LATENCY_BUDGET_SECONDS, (
        f"GET /visits took {elapsed*1000:.0f}ms — over {_LATENCY_BUDGET_SECONDS}s budget"
    )


@pytest.mark.asyncio
async def test_active_endpoint_under_budget(bulk_loaded, requester):
    """`GET /visits/active` (status filter + visibility scope) stays fast."""
    _, client = requester
    resp, elapsed = await _timed(client.get("/api/v1/visits/active"))
    assert resp.status_code == 200
    assert elapsed < _LATENCY_BUDGET_SECONDS, (
        f"GET /visits/active took {elapsed*1000:.0f}ms — over {_LATENCY_BUDGET_SECONDS}s budget"
    )


@pytest.mark.asyncio
async def test_dashboard_overview_under_budget(bulk_loaded, requester):
    """`GET /dashboard/overview` runs 4 COUNT(*) queries — confirm they're all indexed."""
    _, client = requester
    resp, elapsed = await _timed(client.get("/api/v1/dashboard/overview"))
    assert resp.status_code == 200
    assert elapsed < _LATENCY_BUDGET_SECONDS, (
        f"GET /dashboard/overview took {elapsed*1000:.0f}ms — over {_LATENCY_BUDGET_SECONDS}s budget"
    )
