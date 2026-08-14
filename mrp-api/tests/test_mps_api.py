"""MPS run API — weekly (design §2.0/§6.5, the 2026-08-12 rework).

End-to-end coverage over `app/api/v1/mps.py`, which wires the pure
`app/services/mps_engine.py::generate_mps` to real data: a confirmed
forecast version's monthly net requirements (via
`app/services/net_requirement.py`, same opening-stock definition the
`/net-requirement` endpoint uses), **per-week** factory capacity
(`app/services/capacity.py::resolve_limits_for_week`, week exceptions
included), and mdm-api shelf life.

`resolve_shelf_life` and `resolve_material_names` are imported as bare names
into `mps.py` (same idiom `consignment.py` uses for `lookup_lot` — see that
module's docstring) so they can be monkeypatched here without ever hitting
real mdm-api. `_stub_material_names` below does that for the whole module:
`stats["no_shelf_life"]` triggers a name lookup on most of these fixtures,
and an unstubbed one would make every generate wait on a doomed HTTP call.

**Every month-shaped assertion in this file was rewritten**, not adapted:
`plan_month` no longer exists (migration `mrp10b` dropped it),
`production_lead_months` is now `production_lead_weeks`, and
`capacity_occupancy` is keyed by week. Where a monthly test asserted a
placement month, the weekly rewrite asserts the placement WEEK where the
fixture pins one deterministically, and the plan week's OWNING MONTH where
levelling across a whole-month canvas legitimately leaves the exact week
free (see `_bucket_month_of`).

Fixture forecast versions are built directly against the ORM (`db_session`)
rather than via `POST /forecast/versions` + `PUT .../cells` +
`POST .../confirm` — those write/confirm endpoints were retired in the
Continuous Sales Forecast redesign (see tests/test_forecast.py's module
docstring). mps.py only ever reads a version's stored rows, so a version
built this way is indistinguishable to it from one freeze_outlook would
have produced.
"""
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import openpyxl
import pytest

from app.api.v1 import mps as mps_module
from app.models.forecast import ForecastLine, ForecastVersion
from app.services.week_calendar import (
    owning_month, shift_weeks, week_label, week_start_of, weeks_of_month,
)

# The default every run gets when nobody has written mrp_planning_params.
MODE = "iso_thursday"


async def _no_shelf_life(token):
    """Every material's shelf life comes back unknown (None).

    Unknown shelf life does NOT mean "no plan": the engine still schedules
    the product, it just refuses to place it a single week earlier than its
    lead-shifted target (fail safe, see `_placement_allowed`). Under a
    whole-month canvas that makes placement extremely deterministic — the
    only legal week is the target week itself — which several tests below
    rely on. It is also exactly the condition `stats["no_shelf_life"]`
    exists to report."""
    return {}


async def _shelf_life_18(token):
    """S0093 has an 18-month shelf life — unlike `_no_shelf_life`, this lets
    the engine level across a bucket and pre-build across one. Needed by any
    test that expects production somewhere other than the target week."""
    return {"S0093": 18}


@pytest.fixture(autouse=True)
def _stub_material_names(monkeypatch):
    """mdm-api is never reachable from the test process. `no_shelf_life`
    stats resolve names for the products they name, so without this every
    generate in this file would block on a doomed HTTP call. Tests that care
    about the names themselves re-patch this with their own map."""
    async def _none(token):
        return {}
    monkeypatch.setattr(mps_module, "resolve_material_names", _none)


async def _confirmed_version(db_session, *, start="2026-09", months=3, material="S0093", monthly_qty="100"):
    month_list = mps_module._generate_months(start, months)
    version = ForecastVersion(
        version_no=f"FCV-{start}-{uuid.uuid4().hex[:8].upper()}",
        status="confirmed",
        horizon_start_month=start,
        horizon_months=months,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    await db_session.flush()  # assign version.id for the lines' FK below
    for m in month_list:
        db_session.add(ForecastLine(
            version_id=version.id, material_code=material, month=m, qty=Decimal(monthly_qty),
        ))
    await db_session.commit()
    await db_session.refresh(version)
    return {"id": str(version.id)}, month_list


def _shift_month(month: str, delta: int) -> str:
    """'YYYY-MM' + delta months."""
    year, mon = (int(p) for p in month.split("-"))
    idx = year * 12 + (mon - 1) + delta
    y, m0 = divmod(idx, 12)
    return f"{y:04d}-{m0 + 1:02d}"


def _future_month(offset_months: int) -> str:
    """A `YYYY-MM` `offset_months` ahead of whenever the test actually runs —
    computed from the same wall clock `app/api/v1/mps.py` uses for
    `current_week`, so lead-time tests stay deterministic regardless of the
    calendar date the suite happens to run on."""
    return _shift_month(datetime.now(timezone.utc).strftime("%Y-%m"), offset_months)


def _current_week() -> date:
    return week_start_of(datetime.now(timezone.utc).date(), MODE)


def _target_week_of(demand_month: str, lead_weeks: int) -> date:
    """The engine's step-2 target: the demand month's last week shifted back
    `lead_weeks`, never before the current week. Recomputed here rather than
    imported from `mps.py` so a bug in `mps._target_week` cannot make these
    assertions agree with it by construction."""
    target = shift_weeks(weeks_of_month(demand_month, MODE)[-1], -lead_weeks, MODE)
    current = _current_week()
    return current if target < current else target


def _bucket_month_of(demand_month: str, lead_weeks: int) -> str:
    """The owning month of the target week — i.e. the month the engine packs
    this demand into, which is where every non-overflowing line for it must
    land regardless of how the bucket levels internally."""
    return owning_month(_target_week_of(demand_month, lead_weeks), MODE)


async def _factory_rule(client, headers, *, max_sku_count=50, max_output_qty="1000000"):
    """Standing factory-wide limits. NOTE these are now PER WEEK, not per
    month — migration mrp10b deactivates the pre-existing monthly rows for
    exactly this reason."""
    await client.post(
        "/api/v1/capacity/rules",
        json={
            "scope_type": "factory", "constraint_type": "max_sku_count",
            "limit_value": str(max_sku_count), "effective_from": "2026-01-01",
        },
        headers=headers,
    )
    await client.post(
        "/api/v1/capacity/rules",
        json={
            "scope_type": "factory", "constraint_type": "max_output_qty",
            "limit_value": max_output_qty, "uom": "KG", "effective_from": "2026-01-01",
        },
        headers=headers,
    )


async def _maintenance_week(client, headers, week_start: date, *, reason="annual shutdown"):
    r = await client.post(
        "/api/v1/capacity/exceptions",
        json={
            "week_start": week_start.isoformat(), "scope_type": "factory",
            "constraint_type": "max_output_qty", "limit_value": "0",
            "uom": "KG", "reason": reason,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _deny_everything(monkeypatch):
    """Same two seams tests/test_permission_gates.py patches: `admin_token`
    short-circuits every gate, so a denial test must use `non_admin_token`
    AND an empty effective matrix."""
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)


# ── Generate / release end to end ────────────────────────────────────────


@pytest.mark.anyio
async def test_generate_run_and_confirm_release_end_to_end(client, db_session, admin_token, monkeypatch):
    """The whole weekly round trip: a line carries a plan WEEK, that week's
    owning month, a rendered label and a `weeks_early` distance; releasing it
    materializes `mrp_demands` rows that carry `plan_week_start` too."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    version, months = await _confirmed_version(db_session)
    await _factory_rule(client, headers)

    r = await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "draft"
    assert run["run_no"].startswith("MPS-")
    assert Decimal(run["safety_margin_fraction"]) == Decimal("0.3333")
    assert run["production_lead_weeks"] == 0
    assert run["week_calendar_mode"] == MODE
    lines = run["lines"]
    assert len(lines) == 3
    # No opening stock anywhere -> net_requirement == forecast qty each
    # month. Shelf life is unknown, so the only legal week for a demand
    # month is its own lead-shifted target week (lead 0 -> that month's last
    # week): pinned exactly, not merely "somewhere in the month".
    by_month = {l["demand_month"]: l for l in lines}
    for m in months:
        line = by_month[m]
        expected_week = _target_week_of(m, 0)
        assert line["plan_week_start"] == expected_week.isoformat()
        assert line["plan_week_month"] == owning_month(expected_week, MODE) == m
        assert line["week_label"] == week_label(expected_week, MODE)
        assert line["weeks_early"] == 0
        assert Decimal(line["qty"]) == Decimal("100")
        assert line["material_code"] == "S0093"
        assert line["capacity_gap"] is False
        assert line["is_prebuild"] is False
        assert line["lead_shortfall"] is False  # lead 0 -> never clamped

    run_id = run["id"]
    rel = await client.post(f"/api/v1/mps/runs/{run_id}/confirm-release", headers=headers)
    assert rel.status_code == 200, rel.text
    assert rel.json()["status"] == "released"

    import sqlalchemy as sa
    from app.models.demand import MrpDemand

    rows = (await db_session.execute(
        sa.select(MrpDemand).where(MrpDemand.source_run_id == uuid.UUID(run_id))
    )).scalars().all()
    assert len(rows) == 3
    # Phase 1C reads these: every row must carry BOTH the booked month and
    # the booked week, and they must agree with the line they came from.
    by_week = {row.plan_week_start: row for row in rows}
    assert set(by_week) == {date.fromisoformat(l["plan_week_start"]) for l in lines}
    for line in lines:
        row = by_week[date.fromisoformat(line["plan_week_start"])]
        assert row.demand_month == line["plan_week_month"]
        assert row.qty == Decimal(line["qty"])
        assert row.material_code == line["material_code"]


@pytest.mark.anyio
async def test_generate_run_requires_confirmed_forecast_version(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    v = ForecastVersion(
        version_no=f"FCV-2026-09-{uuid.uuid4().hex[:8].upper()}",
        status="draft",
        horizon_start_month="2026-09",
        horizon_months=1,
    )
    db_session.add(v)
    await db_session.commit()
    await db_session.refresh(v)
    r = await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": str(v.id)}, headers=headers,
    )
    assert r.status_code == 409


@pytest.mark.anyio
async def test_material_with_sufficient_opening_stock_produces_no_line(client, db_session, admin_token, monkeypatch):
    """A material whose opening stock already covers every month's forecast
    has zero net requirement everywhere -> it must not appear in the
    generated MPS at all (generate_mps is only ever handed positive-qty
    DemandItems)."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    forecast_version = ForecastVersion(
        version_no=f"FCV-2026-09-{uuid.uuid4().hex[:8].upper()}",
        status="confirmed",
        horizon_start_month="2026-09",
        horizon_months=2,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(forecast_version)
    await db_session.flush()
    db_session.add_all([
        ForecastLine(version_id=forecast_version.id, material_code="S0093", month="2026-09", qty=Decimal("100")),
        ForecastLine(version_id=forecast_version.id, material_code="S0093", month="2026-10", qty=Decimal("100")),
        ForecastLine(version_id=forecast_version.id, material_code="S0060", month="2026-09", qty=Decimal("50")),
        ForecastLine(version_id=forecast_version.id, material_code="S0060", month="2026-10", qty=Decimal("50")),
    ])
    await db_session.commit()
    version = {"id": str(forecast_version.id)}
    await _factory_rule(client, headers)

    from app.models.wms_inventory import WmsInventoryLot
    # S0060's opening stock (500) covers every month's forecast (50 each) --
    # never generates a net requirement.
    db_session.add(WmsInventoryLot(
        warehouse_id="CANADA", material_code="S0060", lot_no="LOT-AMPLE",
        qty=Decimal("500"), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
        mapped_status="available", expiry_date=date(2027, 1, 1),
        sync_batch_id="b1",
    ))
    await db_session.commit()

    r = await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    lines = r.json()["lines"]
    materials = {l["material_code"] for l in lines}
    assert materials == {"S0093"}
    assert len(lines) == 2  # S0093's two months only -- S0060 contributes nothing


# ── production_lead_weeks ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_production_lead_weeks_shifts_the_plan_week_and_is_echoed(
    client, db_session, admin_token, monkeypatch,
):
    """`production_lead_weeks=4` is stored on the run and moves the plan four
    real weeks earlier than the demand month's last week — far enough, at
    these fixtures, to land the whole bucket in the PREVIOUS month (asserted
    explicitly, so the test cannot pass with the lead ignored)."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}

    # The first month at least three ahead (so the current-week clamp never
    # fires) whose last week minus four lands in the PREVIOUS month. Not
    # every month qualifies -- a 5-week month absorbs a 4-week lead entirely
    # -- and hard-coding an offset would make this test silently stop
    # observing the lead depending on when the suite runs.
    start = next(m for m in (_future_month(k) for k in range(3, 18))
                 if _bucket_month_of(m, 4) == _shift_month(m, -1))
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    r = await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 4},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["production_lead_weeks"] == 4
    lines = run["lines"]
    assert len(lines) == 1

    bucket = _bucket_month_of(months[0], 4)
    assert bucket == _shift_month(months[0], -1)  # by construction above
    assert _bucket_month_of(months[0], 0) == months[0], (
        "fixture guard: with no lead this demand would stay in its own month, so a "
        "line in the previous month can only be the lead's doing"
    )
    line = lines[0]
    assert line["plan_week_month"] == bucket
    assert date.fromisoformat(line["plan_week_start"]) in weeks_of_month(bucket, MODE)
    assert line["lead_shortfall"] is False
    assert line["capacity_gap"] is False


@pytest.mark.anyio
async def test_production_lead_weeks_omitted_defaults_to_four(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(4)
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    r = await client.post(
        "/api/v1/mps/runs", json={"forecast_version_id": version["id"]}, headers=headers,
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["production_lead_weeks"] == 4
    assert run["lines"][0]["plan_week_month"] == _bucket_month_of(months[0], 4)


@pytest.mark.anyio
async def test_lead_zero_plans_inside_the_demand_month(client, db_session, admin_token, monkeypatch):
    """`lead_weeks=0` means production lands INSIDE the demand month (design
    §2.0: the month-based engine's placement unit was the month), not
    "only in that month's last week"."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(4)
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    assert {l["plan_week_month"] for l in run["lines"]} == {months[0]}


@pytest.mark.anyio
async def test_production_lead_weeks_clamped_to_current_week_flags_shortfall(
    client, db_session, admin_token, monkeypatch,
):
    """When the lead-adjusted target would fall before "now", it is clamped
    to the current week and the line is flagged `lead_shortfall=True` —
    demand due THIS month with a four-week lead has no runway left."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(0)  # this month -- four weeks back is in the past
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    r = await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 4},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["production_lead_weeks"] == 4
    current = _current_week()
    assert shift_weeks(weeks_of_month(months[0], MODE)[-1], -4, MODE) < current, (
        "fixture guard: this month's last week minus four is supposed to be in the past"
    )
    for line in run["lines"]:
        assert line["lead_shortfall"] is True
        assert line["capacity_gap"] is False  # ample capacity -- not masking a gap
        assert date.fromisoformat(line["plan_week_start"]) >= current


@pytest.mark.anyio
@pytest.mark.parametrize("bad_lead", [-1, 53])
async def test_generate_run_rejects_out_of_range_production_lead_weeks(
    client, db_session, admin_token, monkeypatch, bad_lead,
):
    """`production_lead_weeks` is bounded 0-52 server-side (design §2.6,
    `MpsRunCreate`'s `Field(ge=0, le=52)`). A negative value would schedule
    production AFTER its demand month, and the UI only clamps client-side.
    Request-body validation runs before the forecast-version lookup, so this
    422s even though `version` here is a real confirmed version."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, _months = await _confirmed_version(db_session)

    r = await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": bad_lead},
        headers=headers,
    )
    assert r.status_code == 422, r.text


# ── The run's calendar is a snapshot ─────────────────────────────────────


@pytest.mark.anyio
async def test_run_snapshots_the_week_calendar_mode_in_force_at_generate_time(
    client, db_session, admin_token, monkeypatch,
):
    """The mode comes from `mrp_planning_params`, is written onto the run,
    and shows up in every line's label. A run generated under `month_fixed`
    must be bucketed on the 1/8/15/22/29 grid, not the ISO one."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.put("/api/v1/params/week_calendar_mode",
                     json={"value": "month_fixed"}, headers=headers)

    start = _future_month(3)
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    assert run["week_calendar_mode"] == "month_fixed"
    for line in run["lines"]:
        week = date.fromisoformat(line["plan_week_start"])
        assert week.day in (1, 8, 15, 22, 29), week
        assert line["week_label"] == week_label(week, "month_fixed")
        assert line["plan_week_month"] == owning_month(week, "month_fixed")


@pytest.mark.anyio
async def test_changing_the_calendar_parameter_does_not_reshape_an_existing_run(
    client, db_session, admin_token, monkeypatch,
):
    """A released (or merely reviewed) plan must not re-bucket itself because
    somebody changed a factory setting afterwards. `GET` and `recalculate`
    both read the mode off the RUN.

    The witness is the week grid itself, not just the echoed string: under
    `month_fixed` every plan week starts on a 1/8/15/22/29, which no ISO
    Monday grid can reproduce for a whole month."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(3)
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    # Generated under the default ISO mode...
    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    assert run["week_calendar_mode"] == MODE
    original_weeks = sorted(l["plan_week_start"] for l in run["lines"])
    original_labels = sorted(l["week_label"] for l in run["lines"])

    # ...then the factory switches convention.
    put = await client.put("/api/v1/params/week_calendar_mode",
                           json={"value": "month_fixed"}, headers=headers)
    assert put.status_code == 200, put.text

    got = await client.get(f"/api/v1/mps/runs/{run['id']}", headers=headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["week_calendar_mode"] == MODE
    assert sorted(l["plan_week_start"] for l in body["lines"]) == original_weeks
    assert sorted(l["week_label"] for l in body["lines"]) == original_labels
    assert all(o["week_label"] == week_label(date.fromisoformat(o["week_start"]), MODE)
               for o in body["capacity_occupancy"])

    recalced = await client.post(f"/api/v1/mps/runs/{run['id']}/recalculate", headers=headers)
    assert recalced.status_code == 200, recalced.text
    after = recalced.json()
    assert after["week_calendar_mode"] == MODE
    assert sorted(l["plan_week_start"] for l in after["lines"]) == original_weeks
    # And a NEW run does pick the new mode up -- otherwise this test would
    # also pass if the parameter write silently did nothing.
    fresh = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    assert fresh["week_calendar_mode"] == "month_fixed"
    assert all(date.fromisoformat(l["plan_week_start"]).day in (1, 8, 15, 22, 29)
               for l in fresh["lines"])


@pytest.mark.anyio
async def test_recalculate_uses_the_runs_stored_production_lead_weeks(
    client, db_session, admin_token, monkeypatch,
):
    """`recalculate_run` reads the lead back off the run (there is no way to
    pass one) and re-derives placement from it."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(4)
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 4},
        headers=headers,
    )).json()
    bucket = _bucket_month_of(months[0], 4)
    assert run["lines"][0]["plan_week_month"] == bucket

    r = await client.post(f"/api/v1/mps/runs/{run['id']}/recalculate", headers=headers)
    assert r.status_code == 200, r.text
    recalced = r.json()
    assert recalced["production_lead_weeks"] == 4
    assert recalced["lines"][0]["plan_week_month"] == bucket
    assert recalced["lines"][0]["lead_shortfall"] is False
    assert recalced["lines"][0]["capacity_gap"] is False


# ── Per-week capacity really is per week ─────────────────────────────────


@pytest.mark.anyio
async def test_a_maintenance_week_is_left_empty_and_the_plan_still_fits(
    client, db_session, admin_token, monkeypatch,
):
    """design §5.1's headline case, and the proof that `mps.py` resolves
    capacity PER WEEK: a `max_output_qty=0` exception on one week takes that
    week out of the plan and the production moves to the others, with no
    capacity gap. A month-resolved ceiling cannot express this at all — it
    would either ignore the exception or fail the whole month."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(4)
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="80")
    await _factory_rule(client, headers, max_sku_count=5, max_output_qty="40")

    bucket_weeks = weeks_of_month(_bucket_month_of(months[0], 0), MODE)
    shut = bucket_weeks[0]

    before = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    assert not any(l["capacity_gap"] for l in before["lines"])
    assert shut.isoformat() in {l["plan_week_start"] for l in before["lines"]}, (
        "fixture guard: without the exception this week must actually be used, "
        "or closing it below proves nothing"
    )

    await _maintenance_week(client, headers, shut)

    after = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    used = {l["plan_week_start"] for l in after["lines"] if not l["capacity_gap"]}
    assert shut.isoformat() not in used
    assert not any(l["capacity_gap"] for l in after["lines"])
    assert sum(Decimal(l["qty"]) for l in after["lines"]) == Decimal("80")


@pytest.mark.anyio
async def test_get_run_includes_capacity_occupancy_per_week(client, db_session, admin_token, monkeypatch):
    """Occupancy is keyed by plan WEEK and measured against that week's own
    limits, exception included."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1, monthly_qty="40")
    await _factory_rule(client, headers, max_sku_count=5, max_output_qty="1000")

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    planned_week = run["lines"][0]["plan_week_start"]

    r = await client.get(f"/api/v1/mps/runs/{run['id']}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    occ = {o["week_start"]: o for o in body["capacity_occupancy"]}
    assert set(occ) == {planned_week}
    row = occ[planned_week]
    assert row["used_sku_count"] == 1
    assert Decimal(row["used_qty"]) == Decimal("40")
    assert row["max_sku_count"] == 5
    assert Decimal(row["max_output_qty"]) == Decimal("1000")
    assert row["week_month"] == owning_month(date.fromisoformat(planned_week), MODE)
    assert row["week_label"] == week_label(date.fromisoformat(planned_week), MODE)


# ── Demand context snapshot ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_run_includes_demand_context_per_line(client, db_session, admin_token, monkeypatch):
    """Each MPS line must carry the demand it was planned against: the gross
    forecast for its demand_month, and the rolled-forward opening stock
    entering that month."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    forecast_version = ForecastVersion(
        version_no=f"FCV-2026-09-{uuid.uuid4().hex[:8].upper()}",
        status="confirmed",
        horizon_start_month="2026-09",
        horizon_months=2,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(forecast_version)
    await db_session.flush()
    db_session.add_all([
        ForecastLine(version_id=forecast_version.id, material_code="S0093", month="2026-09", qty=Decimal("100")),
        ForecastLine(version_id=forecast_version.id, material_code="S0093", month="2026-10", qty=Decimal("100")),
    ])
    await db_session.commit()
    version = {"id": str(forecast_version.id)}
    await _factory_rule(client, headers)

    from app.models.wms_inventory import WmsInventoryLot
    db_session.add(WmsInventoryLot(
        warehouse_id="CANADA", material_code="S0093", lot_no="LOT-PARTIAL",
        qty=Decimal("30"), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
        mapped_status="available", expiry_date=date(2027, 1, 1),
        sync_batch_id="b1",
    ))
    await db_session.commit()

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    assert len(run["lines"]) == 2  # both months produce a net requirement

    r = await client.get(f"/api/v1/mps/runs/{run['id']}", headers=headers)
    assert r.status_code == 200, r.text
    lines_by_demand_month = {l["demand_month"]: l for l in r.json()["lines"]}

    m1, m2 = lines_by_demand_month["2026-09"], lines_by_demand_month["2026-10"]
    assert Decimal(m1["demand_forecast"]) == Decimal("100")
    assert Decimal(m1["opening_stock"]) == Decimal("30")
    assert Decimal(m2["demand_forecast"]) == Decimal("100")
    assert Decimal(m2["opening_stock"]) == Decimal("0")


@pytest.mark.anyio
async def test_get_run_demand_context_is_frozen_snapshot_not_live(client, db_session, admin_token, monkeypatch):
    """A run's `demand_forecast`/`opening_stock` must be the snapshot taken
    at generate time, NOT a live recompute -- otherwise a RELEASED run's
    numbers would silently drift as WMS stock moves after generation."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    forecast_version = ForecastVersion(
        version_no=f"FCV-2026-09-{uuid.uuid4().hex[:8].upper()}",
        status="confirmed",
        horizon_start_month="2026-09",
        horizon_months=1,
        confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(forecast_version)
    await db_session.flush()
    db_session.add(
        ForecastLine(version_id=forecast_version.id, material_code="S0093", month="2026-09", qty=Decimal("100")),
    )
    await db_session.commit()
    version = {"id": str(forecast_version.id)}
    await _factory_rule(client, headers)

    from app.models.wms_inventory import WmsInventoryLot
    lot = WmsInventoryLot(
        warehouse_id="CANADA", material_code="S0093", lot_no="LOT-BEFORE",
        qty=Decimal("30"), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
        mapped_status="available", expiry_date=date(2027, 1, 1),
        sync_batch_id="b1",
    )
    db_session.add(lot)
    await db_session.commit()

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    run_id = run["id"]

    r1 = await client.get(f"/api/v1/mps/runs/{run_id}", headers=headers)
    assert r1.status_code == 200, r1.text
    line_before = r1.json()["lines"][0]
    assert Decimal(line_before["opening_stock"]) == Decimal("30")
    assert Decimal(line_before["demand_forecast"]) == Decimal("100")

    lot.qty = Decimal("999")
    db_session.add(WmsInventoryLot(
        warehouse_id="CANADA", material_code="S0093", lot_no="LOT-AFTER",
        qty=Decimal("500"), qty_allocated=Decimal("0"), qty_onhold=Decimal("0"),
        mapped_status="available", expiry_date=date(2027, 1, 1),
        sync_batch_id="b2",
    ))
    await db_session.commit()

    r2 = await client.get(f"/api/v1/mps/runs/{run_id}", headers=headers)
    assert r2.status_code == 200, r2.text
    line_after = r2.json()["lines"][0]
    assert Decimal(line_after["opening_stock"]) == Decimal("30")  # frozen, not live
    assert Decimal(line_after["demand_forecast"]) == Decimal("100")
    assert line_after["opening_stock"] == line_before["opening_stock"]


# ── Adjust (PATCH .../lines/{id}) ────────────────────────────────────────


@pytest.mark.anyio
async def test_patch_line_marks_manual_adjusted_and_can_lock(client, db_session, admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1)
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    line = run["lines"][0]

    r = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"qty": "77", "locked_by_planner": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["qty"]) == Decimal("77")
    assert body["manual_adjusted"] is True
    assert body["locked_by_planner"] is True


@pytest.mark.anyio
async def test_patch_line_moves_a_line_to_another_week_and_re_derives_the_month(
    client, db_session, admin_token, monkeypatch,
):
    """design §5.2: the adjust drawer changes the production WEEK, across a
    month boundary if the planner wants. The server does not just store the
    date — it recomputes the owning month under the run's own calendar and
    the `weeks_early` distance from the demand month's target week."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(4)
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)
    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    line = run["lines"][0]

    target = _target_week_of(months[0], 0)
    # Three weeks earlier than the target: inside 18-month shelf life, and
    # (at these fixtures) in the month BEFORE the demand month's bucket, so
    # the month recomputation has something to get wrong.
    moved_to = shift_weeks(target, -3, MODE)

    r = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"plan_week_start": moved_to.isoformat()},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_week_start"] == moved_to.isoformat()
    assert body["plan_week_month"] == owning_month(moved_to, MODE)
    assert body["week_label"] == week_label(moved_to, MODE)
    assert body["weeks_early"] == 3
    assert body["manual_adjusted"] is True
    # `is_prebuild` is the cross-bucket question, so it is true exactly when
    # the new week's month precedes the demand's bucket month.
    assert body["is_prebuild"] is (owning_month(moved_to, MODE) < _bucket_month_of(months[0], 0))

    # ...and it survives a re-read (i.e. it was persisted, not just echoed).
    got = await client.get(f"/api/v1/mps/runs/{run['id']}", headers=headers)
    stored = next(l for l in got.json()["lines"] if l["id"] == line["id"])
    assert stored["plan_week_start"] == moved_to.isoformat()
    assert stored["plan_week_month"] == owning_month(moved_to, MODE)
    assert stored["weeks_early"] == 3


@pytest.mark.anyio
async def test_patch_line_rejects_a_week_that_is_not_a_week_start(
    client, db_session, admin_token, monkeypatch,
):
    """An off-grid date would put the line in a "week" no capacity resolver,
    occupancy rollup or export column can ever match."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1)
    await _factory_rule(client, headers)
    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    line = run["lines"][0]

    off_grid = date.fromisoformat(line["plan_week_start"]) + timedelta(days=2)
    assert week_start_of(off_grid, MODE) != off_grid  # fixture guard
    r = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"plan_week_start": off_grid.isoformat()},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "not the start of a week" in r.text

    # Not a date at all -> Pydantic's own 422, before anything is touched.
    r2 = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"plan_week_start": "2026-13"},
        headers=headers,
    )
    assert r2.status_code == 422, r2.text


@pytest.mark.anyio
async def test_patch_line_refuses_a_week_shelf_life_does_not_allow(
    client, db_session, admin_token, monkeypatch,
):
    """A hand-move runs the SAME shelf-life rule the engine places by
    (`mps_engine._placement_allowed`) and is rejected outright when it fails.

    It is deliberately not stored with `shelf_life_ok=False`: the engine only
    ever puts that flag on a `capacity_gap` line, so a produced line failing
    the rule would be a row shape nothing downstream knows how to read — for
    product that expires before the month it was made for."""
    async def _one_month_shelf_life(token):
        return {"S0093": 1}

    monkeypatch.setattr(mps_module, "resolve_shelf_life", _one_month_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(5)
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)
    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    line = run["lines"][0]
    before = line["plan_week_start"]

    # A one-month shelf life at the default 1/3 margin allows roughly 20 days
    # before the demand month starts; ten weeks earlier is far outside it.
    too_early = shift_weeks(_target_week_of(months[0], 0), -10, MODE)
    r = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"plan_week_start": too_early.isoformat()},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "shelf life" in r.text

    got = await client.get(f"/api/v1/mps/runs/{run['id']}", headers=headers)
    stored = next(l for l in got.json()["lines"] if l["id"] == line["id"])
    assert stored["plan_week_start"] == before  # nothing was written
    assert stored["manual_adjusted"] is False


@pytest.mark.anyio
async def test_patch_line_refuses_to_lock_a_capacity_gap_line(
    client, db_session, admin_token, monkeypatch,
):
    """`generate_mps` DROPS a locked `capacity_gap` line (a stale shortfall
    echoed alongside re-planned demand double-counts it). So a lock the API
    accepted here would be silently cleared on the next recalculate, taking
    `manual_adjusted` and the demand-context snapshot with it. Refuse it
    where the planner can see the refusal."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1, monthly_qty="100")
    # Not enough weekly capacity for 100 anywhere, and unknown shelf life
    # forbids pre-building it -- the only possible outcome is a gap line.
    await _factory_rule(client, headers, max_sku_count=50, max_output_qty="50")

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    gap = next(l for l in run["lines"] if l["capacity_gap"])

    r = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{gap['id']}",
        json={"locked_by_planner": True},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "cannot be locked" in r.text

    # Unlocking is always allowed, and an unrelated edit on a gap row still
    # works -- only the lock is refused.
    ok = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{gap['id']}",
        json={"locked_by_planner": False},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["locked_by_planner"] is False


# ── Locked lines survive a recalculate ───────────────────────────────────


@pytest.mark.anyio
async def test_recalculate_keeps_a_locked_line_fixed(client, db_session, admin_token, monkeypatch):
    """Shipped behaviour that must not regress: a locked line keeps its
    quantity AND its week through a recalculate, and keeps its
    `manual_adjusted` flag and demand-context snapshot with them."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    line = run["lines"][0]

    locked = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"qty": "999", "locked_by_planner": True},
        headers=headers,
    )
    assert locked.status_code == 200, locked.text

    r = await client.post(f"/api/v1/mps/runs/{run['id']}/recalculate", headers=headers)
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    assert len(lines) == 1
    after = lines[0]
    assert after["locked_by_planner"] is True
    assert Decimal(after["qty"]) == Decimal("999")  # untouched by recalculation
    assert after["plan_week_start"] == line["plan_week_start"]  # and not moved
    assert after["manual_adjusted"] is True
    assert Decimal(after["demand_forecast"]) == Decimal(line["demand_forecast"])


@pytest.mark.anyio
async def test_recalculate_replans_only_the_unlocked_remainder_of_a_demand_month(
    client, db_session, admin_token, monkeypatch,
):
    """Weekly, one (material, demand month) spans several lines, so a month
    can be part locked and part open. The locked part is echoed untouched and
    ONLY the remainder is re-planned — the month-based code dropped the whole
    `(material, demand_month)` demand key, which deleted the open part."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(4)
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="120")
    await _factory_rule(client, headers, max_sku_count=5, max_output_qty="40")

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    lines = run["lines"]
    assert len(lines) > 1, "fixture guard: 120 against a 40/week cap must span weeks"
    assert sum(Decimal(l["qty"]) for l in lines) == Decimal("120")

    one = lines[0]
    lock = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{one['id']}",
        json={"locked_by_planner": True}, headers=headers,
    )
    assert lock.status_code == 200, lock.text

    r = await client.post(f"/api/v1/mps/runs/{run['id']}/recalculate", headers=headers)
    assert r.status_code == 200, r.text
    after = r.json()["lines"]
    # Total demand is conserved: the locked slice was not double-counted (it
    # would sum over 120) and the open remainder was not deleted (under 120).
    assert sum(Decimal(l["qty"]) for l in after) == Decimal("120")
    kept = [l for l in after if l["locked_by_planner"]]
    assert len(kept) == 1
    assert kept[0]["plan_week_start"] == one["plan_week_start"]
    assert Decimal(kept[0]["qty"]) == Decimal(one["qty"])


@pytest.mark.anyio
async def test_recalculate_keeps_a_locked_lead_shortfall_line_fixed(client, db_session, admin_token, monkeypatch):
    """`lead_shortfall` is a `WeeklyLine` field, so the engine echoes it back
    on a locked line; this pins that it does not silently reset to False."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}

    start = _future_month(0)  # this month -- a four-week lead has no runway
    version, months = await _confirmed_version(db_session, start=start, months=1, monthly_qty="100")
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 4},
        headers=headers,
    )).json()
    line = run["lines"][0]
    assert line["lead_shortfall"] is True  # sanity: the clamp fired

    lock = await client.patch(
        f"/api/v1/mps/runs/{run['id']}/lines/{line['id']}",
        json={"locked_by_planner": True},
        headers=headers,
    )
    assert lock.status_code == 200, lock.text
    assert lock.json()["lead_shortfall"] is True  # PATCH echoes the line unchanged

    r = await client.post(f"/api/v1/mps/runs/{run['id']}/recalculate", headers=headers)
    assert r.status_code == 200, r.text
    recalced_lines = r.json()["lines"]
    assert len(recalced_lines) == 1
    recalced_line = recalced_lines[0]
    assert recalced_line["locked_by_planner"] is True
    assert recalced_line["plan_week_start"] == line["plan_week_start"]
    assert recalced_line["lead_shortfall"] is True  # must NOT have reset to False


# ── Release ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_confirm_release_skips_capacity_gap_lines(client, db_session, admin_token, monkeypatch):
    """A capacity_gap line is an unmet-demand exception for a human to
    resolve, not a booked production order -- it must never be materialized
    into mrp_demands, or Phase 1C's material explosion would over-procure raw
    materials for production that can't actually happen this cycle. It IS
    still persisted as an MrpMpsLine, so it stays visible to the planner."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1, monthly_qty="100")
    # 50/week against 100 of demand, and unknown shelf life bars pre-building
    # the rest into an earlier week: half is produced, half is an explicit gap.
    await _factory_rule(client, headers, max_sku_count=50, max_output_qty="50")

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    gaps = [l for l in run["lines"] if l["capacity_gap"]]
    produced = [l for l in run["lines"] if not l["capacity_gap"]]
    assert len(gaps) == 1 and len(produced) == 1
    assert sum(Decimal(l["qty"]) for l in run["lines"]) == Decimal("100")

    rel = await client.post(f"/api/v1/mps/runs/{run['id']}/confirm-release", headers=headers)
    assert rel.status_code == 200, rel.text

    import sqlalchemy as sa
    from app.models.demand import MrpDemand
    from app.models.mps import MrpMpsLine

    demand_rows = (await db_session.execute(
        sa.select(MrpDemand).where(MrpDemand.source_run_id == uuid.UUID(run["id"]))
    )).scalars().all()
    # Only the produced line materializes -- the gap's quantity is nowhere in
    # mrp_demands, or Phase 1C would buy raw materials for it.
    assert len(demand_rows) == 1
    assert demand_rows[0].qty == Decimal(produced[0]["qty"])
    assert demand_rows[0].plan_week_start == date.fromisoformat(produced[0]["plan_week_start"])

    line_rows = (await db_session.execute(
        sa.select(MrpMpsLine).where(MrpMpsLine.run_id == uuid.UUID(run["id"]))
    )).scalars().all()
    assert len(line_rows) == 2
    assert sum(1 for r in line_rows if r.capacity_gap) == 1  # still visible as an MrpMpsLine


@pytest.mark.anyio
async def test_confirm_release_clears_prior_cycle_demand_across_forecast_versions(
    client, db_session, admin_token, monkeypatch,
):
    """Rolling-forecast cycle: v1 confirmed -> R1 released -> forecast
    revised -> v2 confirmed (a NEW version_id) -> R2 released. mrp_demands
    must end with ONLY R2's rows: R1's must not survive just because they
    belong to a different, now-superseded forecast_version_id -- that would
    silently double-count demand on every cycle. The clear is unconditional
    on `demand_type='mps'` and this invariant is unchanged by weekly."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}

    v1, _ = await _confirmed_version(
        db_session, start="2026-09", months=1, material="S0093", monthly_qty="100",
    )
    await _factory_rule(client, headers)
    r1 = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": v1["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    rel1 = await client.post(f"/api/v1/mps/runs/{r1['id']}/confirm-release", headers=headers)
    assert rel1.status_code == 200, rel1.text

    v2, months2 = await _confirmed_version(
        db_session, start="2026-10", months=1, material="S0093", monthly_qty="120",
    )
    r2 = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": v2["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    rel2 = await client.post(f"/api/v1/mps/runs/{r2['id']}/confirm-release", headers=headers)
    assert rel2.status_code == 200, rel2.text

    import sqlalchemy as sa
    from app.models.demand import MrpDemand

    rows = (await db_session.execute(sa.select(MrpDemand))).scalars().all()
    assert len(rows) == 1  # R1's row was cleared, not just left orphaned under v1
    assert rows[0].source_run_id == uuid.UUID(r2["id"])
    assert rows[0].qty == Decimal("120.000")
    # Every written row carries the plan week, not only the month.
    assert rows[0].plan_week_start == date.fromisoformat(r2["lines"][0]["plan_week_start"])
    assert rows[0].demand_month == r2["lines"][0]["plan_week_month"]


@pytest.mark.anyio
async def test_confirm_release_requires_permission(client, db_session, admin_token, non_admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, _ = await _confirmed_version(db_session)
    await _factory_rule(client, headers)
    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()

    _deny_everything(monkeypatch)
    denied_headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.post(f"/api/v1/mps/runs/{run['id']}/confirm-release", headers=denied_headers)
    assert r.status_code == 403


# ── Shelf-life reporting (design §7) ─────────────────────────────────────


@pytest.mark.anyio
async def test_generate_reports_products_without_shelf_life(client, db_session, admin_token, monkeypatch):
    """design §7: name the products planned with no shelf life on record.

    ERP's `exp` field has never been verified to hold values for finished
    goods (design §9). When it is empty the engine still plans the product
    but refuses to move it a single week early — a silent degradation that
    looks exactly like ordinary capacity pressure from the outside. This
    stat is what makes it visible."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)

    async def _named(token):
        return {"S0093": "Whole Milk Powder 25kg"}

    monkeypatch.setattr(mps_module, "resolve_material_names", _named)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, _months = await _confirmed_version(db_session, months=1)
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    assert run["stats"]["no_shelf_life"] == [
        {"code": "S0093", "name": "Whole Milk Powder 25kg"},
    ]

    # recalculate re-derives it rather than carrying the old stats forward.
    recalced = (await client.post(
        f"/api/v1/mps/runs/{run['id']}/recalculate", headers=headers)).json()
    assert [x["code"] for x in recalced["stats"]["no_shelf_life"]] == ["S0093"]


@pytest.mark.anyio
async def test_generate_reports_no_missing_shelf_life_when_it_is_known(
    client, db_session, admin_token, monkeypatch,
):
    """The other half: a fully-populated shelf-life map reports an EMPTY
    list, so the stat means something when it is non-empty."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _shelf_life_18)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, _months = await _confirmed_version(db_session, months=1)
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    assert run["stats"]["no_shelf_life"] == []


# ── Intent products ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_skips_intent_rows_and_names_them(client, auth_headers, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    intent = (await client.post("/api/v1/intent-products", json={"name": "Not Yet Real"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-09", "qty": "700"},
        {"material_code": "S0093", "month": "2027-09", "qty": "900"},
    ]})
    version = (await client.post("/api/v1/series/outlook",
                                 json={"anchor_month": "2027-09"}, headers=auth_headers)).json()

    run = (await client.post("/api/v1/mps/runs",
                             json={"forecast_version_id": version["id"]},
                             headers=auth_headers)).json()
    codes = {l["material_code"] for l in run["lines"]}
    assert intent["code"] not in codes
    skipped = run["stats"]["skipped_intent"]
    assert [s["code"] for s in skipped] == [intent["code"]]
    assert skipped[0]["name"] == "Not Yet Real"
    # An intent product has no ERP material and never reaches the engine, so
    # it must not be reported as a missing-shelf-life product either.
    assert intent["code"] not in {x["code"] for x in run["stats"]["no_shelf_life"]}


@pytest.mark.asyncio
async def test_recalculate_still_skips_intent_rows(client, auth_headers, monkeypatch):
    """`recalculate_run` re-derives `demands` from `_build_demand_items`
    independently of `create_run`, so the intent exclusion has to be applied
    on THIS path too, or a clean run would silently re-admit the intent
    product the moment a planner recalculates it."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    intent = (await client.post("/api/v1/intent-products", json={"name": "Still Not Real"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-10", "qty": "700"},
        {"material_code": "S0093", "month": "2027-10", "qty": "900"},
    ]})
    version = (await client.post("/api/v1/series/outlook",
                                 json={"anchor_month": "2027-10"}, headers=auth_headers)).json()

    run = (await client.post("/api/v1/mps/runs",
                             json={"forecast_version_id": version["id"]},
                             headers=auth_headers)).json()

    r = await client.post(f"/api/v1/mps/runs/{run['id']}/recalculate", headers=auth_headers)
    assert r.status_code == 200, r.text
    recalced = r.json()
    codes = {l["material_code"] for l in recalced["lines"]}
    assert intent["code"] not in codes
    skipped = recalced["stats"]["skipped_intent"]
    assert [s["code"] for s in skipped] == [intent["code"]]
    assert skipped[0]["name"] == "Still Not Real"


# ── Export ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_export_run_returns_xlsx_matrix_in_tonnes_and_kg(client, db_session, admin_token, monkeypatch):
    """GET .../export renders the production plan matrix: a Product/Metric
    header, then Demand/Available/Planned rows per product across the run's
    plan months. unit=t scales qty/1000 (3dp); unit=kg leaves the raw KG
    value untouched.

    Columns are still MONTHS (each line's plan week's owning month) — design
    §5.1's week columns with a month grouping header are a separate change,
    and `app/services/mps_export.py` was only renamed onto the new field
    here, not restructured."""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)

    async def _fake_names(token):
        return {"S0093": "Whole Milk Powder 25kg"}

    monkeypatch.setattr(mps_module, "resolve_material_names", _fake_names)

    headers = {"Authorization": f"Bearer {admin_token}"}
    version, months = await _confirmed_version(db_session, months=1, monthly_qty="2500")
    await _factory_rule(client, headers)

    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()
    assert len(run["lines"]) == 1
    line = run["lines"][0]
    qty_kg = Decimal(line["qty"])

    r = await client.get(f"/api/v1/mps/runs/{run['id']}/export?unit=t", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert r.headers["content-disposition"] == f'attachment; filename="production-plan-{run["run_no"]}.xlsx"'

    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    ws = wb.active
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    assert header == ["Product", "Metric", line["plan_week_month"]]

    rows = {row[1]: row for row in ws.iter_rows(min_row=2, values_only=True)}
    assert set(rows) == {"Demand", "Available", "Planned"}
    for row in rows.values():
        assert row[0] == "Whole Milk Powder 25kg"  # resolved name, not the bare code
    expected_tonnes = float((qty_kg / Decimal("1000")).quantize(Decimal("0.001")))
    assert rows["Planned"][2] == expected_tonnes
    assert rows["Demand"][2] == float((Decimal("2500") / Decimal("1000")).quantize(Decimal("0.001")))

    r_kg = await client.get(f"/api/v1/mps/runs/{run['id']}/export?unit=kg", headers=headers)
    assert r_kg.status_code == 200, r_kg.text
    wb_kg = openpyxl.load_workbook(io.BytesIO(r_kg.content))
    ws_kg = wb_kg.active
    rows_kg = {row[1]: row for row in ws_kg.iter_rows(min_row=2, values_only=True)}
    assert rows_kg["Planned"][2] == float(qty_kg)


@pytest.mark.anyio
async def test_export_run_requires_permission(client, db_session, admin_token, non_admin_token, monkeypatch):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    version, _ = await _confirmed_version(db_session, months=1)
    await _factory_rule(client, headers)
    run = (await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )).json()

    _deny_everything(monkeypatch)
    denied_headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.get(f"/api/v1/mps/runs/{run['id']}/export", headers=denied_headers)
    assert r.status_code == 403
