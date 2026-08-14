"""Tests for `freeze_outlook` (`app/services/demand_series.py`) and
`POST /series/outlook` (`app/api/v1/series.py`) — Continuous Sales Forecast
redesign, plan doc 2026-08-06-continuous-sales-forecast, Task 4.

"Generate Outlook" freezes the `[anchor, anchor+horizon)` slice of the
living `mrp_demand_series` table into an immutable `ForecastVersion`
snapshot (+ `ForecastLine` rows) — the new source of MPS input. Covers the
brief's required cases: the frozen snapshot exactly matches the series
window (cells outside the window excluded); `source_anchor_month` is set;
a second freeze after editing a series cell produces a NEW version while
the first version's lines are unchanged (immutability); the `POST
/series/outlook` endpoint 201s, and the created version shows up in
`GET /forecast/versions` as `confirmed`; and — the behavioural change this
task makes — freezing (and confirming) does NOT supersede a prior
confirmed version, several stay `confirmed` at once (design §4.3).
"""
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.forecast import ForecastLine, ForecastVersion
from app.services.demand_series import CellChange, freeze_outlook, upsert_cells

# `FCV-{anchor_month, no dash}-{MMDDHH}`, optionally `-N` disambiguated (see
# app/services/numbering.py). Pins the actual shape -- YYYYMM with no
# separating dash, creation month/day/hour zero-padded to exactly 6 digits
# -- not just the literal prefix, so a regression back to the old
# `FCV-2026-09-DDHHMM` shape (dashed month, day/hour/minute) or to the
# original random-hex suffix would fail this pattern even though it still
# starts with "FCV-202609-".
_VERSION_NO_RE = re.compile(r"^FCV-\d{6}-\d{6}(-\d+)?$")


# ── freeze_outlook (service-level) ──────────────────────────────────────────


@pytest.mark.anyio
async def test_freeze_outlook_snapshot_matches_series_window(db_session):
    await upsert_cells(
        db_session,
        [
            CellChange("S0093", "2026-09", Decimal("100")),
            CellChange("S0093", "2026-10", Decimal("50")),
            CellChange("S0060", "2026-11", Decimal("20")),
            # outside the 3-month [2026-09, 2026-12) window -- must not freeze
            CellChange("S0093", "2026-12", Decimal("999")),
        ],
        current_month="2026-09", changed_by=None,
    )

    creator = uuid.uuid4()
    version = await freeze_outlook(db_session, "2026-09", 3, created_by=creator)

    assert version.status == "confirmed"
    assert version.confirmed_at is not None
    assert version.horizon_start_month == "2026-09"
    assert version.horizon_months == 3
    assert version.source_anchor_month == "2026-09"
    assert version.created_by == creator
    assert _VERSION_NO_RE.match(version.version_no)
    assert version.version_no.startswith("FCV-202609-")

    lines = (await db_session.execute(
        select(ForecastLine).where(ForecastLine.version_id == version.id)
    )).scalars().all()
    actual = {(l.material_code, l.month, l.qty) for l in lines}
    assert actual == {
        ("S0093", "2026-09", Decimal("100")),
        ("S0093", "2026-10", Decimal("50")),
        ("S0060", "2026-11", Decimal("20")),
    }


@pytest.mark.anyio
async def test_freeze_outlook_second_freeze_is_a_new_version_first_unchanged(db_session):
    """Immutability: editing the living series after a freeze must never
    reach back into a previously frozen snapshot's lines."""
    await upsert_cells(
        db_session, [CellChange("S0093", "2026-09", Decimal("100"))],
        current_month="2026-09", changed_by=None,
    )
    v1 = await freeze_outlook(db_session, "2026-09", 1, created_by=None)

    await upsert_cells(
        db_session, [CellChange("S0093", "2026-09", Decimal("200"))],
        current_month="2026-09", changed_by=None,
    )
    v2 = await freeze_outlook(db_session, "2026-09", 1, created_by=None)

    assert v1.id != v2.id
    assert _VERSION_NO_RE.match(v1.version_no) and _VERSION_NO_RE.match(v2.version_no)
    assert v1.version_no != v2.version_no
    assert v1.status == "confirmed" and v2.status == "confirmed"

    v1_lines = (await db_session.execute(
        select(ForecastLine).where(ForecastLine.version_id == v1.id)
    )).scalars().all()
    assert len(v1_lines) == 1 and v1_lines[0].qty == Decimal("100")  # untouched by the later edit

    v2_lines = (await db_session.execute(
        select(ForecastLine).where(ForecastLine.version_id == v2.id)
    )).scalars().all()
    assert len(v2_lines) == 1 and v2_lines[0].qty == Decimal("200")


@pytest.mark.anyio
async def test_freeze_outlook_empty_window_creates_version_with_no_lines(db_session):
    """No series cells fall in the window -- a valid (if empty) snapshot is
    still created, not an error; there's nothing invalid about freezing a
    quiet period."""
    version = await freeze_outlook(db_session, "2030-01", 2, created_by=None)
    assert version.status == "confirmed"
    lines = (await db_session.execute(
        select(ForecastLine).where(ForecastLine.version_id == version.id)
    )).scalars().all()
    assert lines == []


@pytest.mark.anyio
async def test_freeze_outlook_two_freezes_in_the_same_hour_both_succeed(db_session, monkeypatch):
    """The scenario the task calls out as now routine, not rare: freezing
    two outlooks for the same anchor inside the same UTC HOUR (MMDDHH has
    no minute of its own) must not 500 with an IntegrityError on
    `version_no`'s unique constraint. The two back-to-back freezes in the
    immutability test above already exercise this most of the time, but
    only by luck of wall-clock timing near an hour boundary -- this test
    pins the clock so the collision is guaranteed, not incidental.

    Mutation this catches: if `freeze_outlook` reverted to computing
    `version_no` inline (bypassing `next_timestamped_no`'s collision check --
    e.g. the pre-fix `uuid4().hex[:6]` shape, or a naive `f"{prefix}{now}"`
    with no existing-rows check), the second `freeze_outlook` call here would
    raise `IntegrityError` instead of returning a second confirmed version."""
    from datetime import datetime as real_datetime, timezone

    from app.services import numbering

    fixed = real_datetime(2026, 9, 3, 9, 15, 0, tzinfo=timezone.utc)

    class _FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(numbering, "datetime", _FrozenDateTime)

    v1 = await freeze_outlook(db_session, "2026-09", 1, created_by=None)
    v2 = await freeze_outlook(db_session, "2026-09", 1, created_by=None)

    assert v1.version_no == "FCV-202609-090309"
    assert v2.version_no == "FCV-202609-090309-2"
    assert v1.status == "confirmed" and v2.status == "confirmed"


# ── POST /series/outlook (API-level) ────────────────────────────────────────


@pytest.mark.anyio
async def test_post_outlook_returns_201_and_appears_confirmed_in_versions_list(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.put(
        "/api/v1/series/cells",
        json={"cells": [
            {"material_code": "S0093", "month": "2099-01", "qty": "100"},
            {"material_code": "S0093", "month": "2099-02", "qty": "50"},
        ]},
        headers=headers,
    )

    r = await client.post(
        "/api/v1/series/outlook",
        json={"anchor_month": "2099-01", "horizon_months": 2},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "confirmed"
    assert body["horizon_start_month"] == "2099-01"
    assert body["horizon_months"] == 2

    listing = (await client.get(
        "/api/v1/forecast/versions", params={"page_size": 50}, headers=headers,
    )).json()
    statuses = {item["id"]: item["status"] for item in listing["items"]}
    assert statuses[body["id"]] == "confirmed"


@pytest.mark.anyio
async def test_post_outlook_defaults_horizon_months_to_18(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/v1/series/outlook", json={"anchor_month": "2099-03"}, headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["horizon_months"] == 18


@pytest.mark.anyio
async def test_post_outlook_bad_anchor_month_returns_422(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/v1/series/outlook", json={"anchor_month": "not-a-month"}, headers=headers,
    )
    assert r.status_code == 422, r.text


@pytest.mark.anyio
async def test_post_outlook_without_write_permission_returns_403(client, non_admin_token, monkeypatch):
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)

    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.post(
        "/api/v1/series/outlook", json={"anchor_month": "2099-04"}, headers=headers,
    )
    assert r.status_code == 403


# ── Freezing no longer supersedes prior confirmed versions ─────────────────


@pytest.mark.anyio
async def test_freezing_outlook_does_not_supersede_prior_confirmed_forecast_version(client, db_session, admin_token):
    """Design §4.3: outlook snapshots coexist -- several `confirmed`
    versions may exist at once. A version that is already `confirmed` --
    built directly against the ORM here, since POST /versions and
    POST .../confirm were retired with the continuous series (Task 8); the
    only way current code produces a confirmed version is freeze_outlook --
    must stay `confirmed` after a later outlook freeze, and the freshly
    frozen outlook is also `confirmed` -- neither flips the other to
    `superseded`."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    v1 = ForecastVersion(
        version_no=f"FCV-2026-09-{uuid.uuid4().hex[:8].upper()}",
        status="confirmed",
        horizon_start_month="2026-09",
        horizon_months=18,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(v1)
    await db_session.commit()
    await db_session.refresh(v1)

    r2 = await client.post(
        "/api/v1/series/outlook",
        json={"anchor_month": "2099-05", "horizon_months": 1},
        headers=headers,
    )
    assert r2.status_code == 201, r2.text
    v2 = r2.json()
    assert v2["status"] == "confirmed"

    listing = (await client.get(
        "/api/v1/forecast/versions", params={"page_size": 50}, headers=headers,
    )).json()
    statuses = {item["id"]: item["status"] for item in listing["items"]}
    assert statuses[str(v1.id)] == "confirmed"  # NOT superseded by the later outlook freeze
    assert statuses[v2["id"]] == "confirmed"


@pytest.mark.asyncio
async def test_outlook_snapshot_flags_intent_lines(client, auth_headers, db_session):
    from sqlalchemy import text
    intent = (await client.post("/api/v1/intent-products", json={"name": "Planned SKU"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-06", "qty": "500"},
        {"material_code": "S0093", "month": "2027-06", "qty": "800"},
    ]})
    r = await client.post("/api/v1/series/outlook",
                          json={"anchor_month": "2027-06"}, headers=auth_headers)
    assert r.status_code == 201, r.text

    rows = (await db_session.execute(text(
        "select material_code, is_intent, intent_name from mrp_forecast_lines "
        "where version_id = :v"
    ), {"v": r.json()["id"]})).all()
    by_code = {c: (f, n) for c, f, n in rows}
    assert by_code[intent["code"]] == (True, "Planned SKU")
    assert by_code["S0093"][0] is False
