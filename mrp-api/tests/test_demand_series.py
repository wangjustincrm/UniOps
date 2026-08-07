"""Tests for `app/services/demand_series.py` (Continuous Sales Forecast
redesign, plan doc 2026-08-06-continuous-sales-forecast, Task 2).

Covers the brief's required cases verbatim (new cell -> row + one log;
past-month rejected; non-KG rejected) plus the edge cases the interface
implies: editing an existing cell logs old->new and updates the row; qty=0
deletes the row and logs (old,new=0); qty=0 against a material with no
existing row is a true no-op (nothing to delete, nothing changed); a same-qty
resubmit is a no-op (writes/logs nothing); a batch with one bad cell rejects
the WHOLE batch atomically (the valid cell in the same batch is not written
either); `read_series_grid`'s GridResponse shape/totals over a range; and a
malformed month (e.g. "2026-1", which string-compares as "after" a real
current_month like "2026-08") is rejected both at the HTTP body-validation
layer (SeriesCellUpsert.month) and, belt-and-suspenders, inside upsert_cells
itself — closing the past-read-only bypass a malformed month would otherwise
open.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.v1 import series
from app.models.demand_series import MrpDemandSeries, MrpForecastChangeLog
from app.services.demand_series import (
    CellChange,
    PastMonthError,
    UomError,
    generate_month_range,
    read_series_grid,
    upsert_cells,
)


# ── generate_month_range (pure function) ────────────────────────────────────


def test_generate_month_range_inclusive():
    assert generate_month_range("2026-09", "2026-11") == ["2026-09", "2026-10", "2026-11"]


def test_generate_month_range_single_month():
    assert generate_month_range("2026-09", "2026-09") == ["2026-09"]


def test_generate_month_range_crosses_year_boundary():
    assert generate_month_range("2026-11", "2027-02") == [
        "2026-11", "2026-12", "2027-01", "2027-02",
    ]


def test_generate_month_range_reversed_is_empty():
    assert generate_month_range("2026-11", "2026-09") == []


# ── upsert_cells: brief's cases verbatim ────────────────────────────────────


@pytest.mark.anyio
async def test_new_cell_writes_row_and_one_log(db_session):
    res = await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("100"))],
        current_month="2026-09", changed_by=None,
    )
    await db_session.commit()
    assert (res.upserted, res.changed) == (1, 1)
    grid = await read_series_grid(db_session, "2026-11", "2026-11")
    assert grid["rows"][0]["cells"]["2026-11"] == Decimal("100")
    # exactly one change-log row, old None -> new 100
    logs = (await db_session.execute(select(MrpForecastChangeLog))).scalars().all()
    assert len(logs) == 1 and logs[0].old_qty is None and logs[0].new_qty == Decimal("100")


@pytest.mark.anyio
async def test_past_month_write_rejected(db_session):
    with pytest.raises(PastMonthError):
        await upsert_cells(db_session, [CellChange("S0093", "2026-08", Decimal("5"))],
                            current_month="2026-09", changed_by=None)


@pytest.mark.anyio
async def test_non_kg_rejected(db_session):
    with pytest.raises(UomError):
        await upsert_cells(db_session, [CellChange("S0093", "2026-11", Decimal("5"), uom="EA")],
                            current_month="2026-09", changed_by=None)


@pytest.mark.anyio
async def test_changed_by_name_is_stored_on_the_log_row(db_session):
    """mrp06 follow-up: upsert_cells' changed_by_name param (write-time
    denormalization, see app/models/demand_series.py's
    MrpForecastChangeLog.changed_by_name docstring) must land on the log row
    it produces, independent of changed_by itself."""
    await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("100"))],
        current_month="2026-09", changed_by=uuid.uuid4(), changed_by_name="Jane Planner",
    )
    logs = (await db_session.execute(select(MrpForecastChangeLog))).scalars().all()
    assert len(logs) == 1 and logs[0].changed_by_name == "Jane Planner"


@pytest.mark.anyio
async def test_changed_by_name_defaults_to_none(db_session):
    """A caller that doesn't pass changed_by_name (e.g. a non-HTTP caller,
    or identity-api down) must still write the log row -- just without a
    name, never a broken save."""
    await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("100"))],
        current_month="2026-09", changed_by=None,
    )
    logs = (await db_session.execute(select(MrpForecastChangeLog))).scalars().all()
    assert len(logs) == 1 and logs[0].changed_by_name is None


# ── upsert_cells: edit / delete / no-op / atomic-batch edge cases ──────────


@pytest.mark.anyio
async def test_editing_existing_cell_updates_row_and_appends_second_log(db_session):
    await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("100"))],
        current_month="2026-09", changed_by=None,
    )

    changer = uuid.uuid4()
    res = await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("150"))],
        current_month="2026-09", changed_by=changer,
    )
    assert (res.upserted, res.changed) == (1, 1)

    grid = await read_series_grid(db_session, "2026-11", "2026-11")
    assert grid["rows"][0]["cells"]["2026-11"] == Decimal("150")

    # underlying series row was updated in place, not duplicated
    series_rows = (await db_session.execute(select(MrpDemandSeries))).scalars().all()
    assert len(series_rows) == 1 and series_rows[0].qty == Decimal("150")

    logs = (await db_session.execute(
        select(MrpForecastChangeLog).order_by(MrpForecastChangeLog.changed_at)
    )).scalars().all()
    assert len(logs) == 2
    assert logs[0].old_qty is None and logs[0].new_qty == Decimal("100")
    assert logs[1].old_qty == Decimal("100") and logs[1].new_qty == Decimal("150")
    assert logs[1].changed_by == changer


@pytest.mark.anyio
async def test_qty_zero_deletes_existing_row_and_logs_old_to_zero(db_session):
    await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("100"))],
        current_month="2026-09", changed_by=None,
    )

    res = await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("0"))],
        current_month="2026-09", changed_by=None,
    )
    assert (res.upserted, res.changed) == (1, 1)

    # sparse table: the row is gone, not stored as qty=0
    series_rows = (await db_session.execute(select(MrpDemandSeries))).scalars().all()
    assert series_rows == []

    grid = await read_series_grid(db_session, "2026-11", "2026-11")
    assert grid["rows"] == []  # nothing to render once the row is deleted

    logs = (await db_session.execute(
        select(MrpForecastChangeLog).order_by(MrpForecastChangeLog.changed_at)
    )).scalars().all()
    assert len(logs) == 2
    assert logs[1].old_qty == Decimal("100") and logs[1].new_qty == Decimal("0")


@pytest.mark.anyio
async def test_qty_zero_with_no_existing_row_is_a_true_noop(db_session):
    """Setting a never-written cell to 0 has nothing to delete and nothing to
    change -- it must not create a row or a log entry."""
    res = await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("0"))],
        current_month="2026-09", changed_by=None,
    )
    assert (res.upserted, res.changed) == (0, 0)
    assert (await db_session.execute(select(MrpDemandSeries))).scalars().all() == []
    assert (await db_session.execute(select(MrpForecastChangeLog))).scalars().all() == []


@pytest.mark.anyio
async def test_resubmitting_same_qty_writes_nothing(db_session):
    await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("100"))],
        current_month="2026-09", changed_by=None,
    )

    res = await upsert_cells(
        db_session, [CellChange("S0093", "2026-11", Decimal("100"))],
        current_month="2026-09", changed_by=None,
    )
    assert (res.upserted, res.changed) == (0, 0)

    # still exactly one series row and one log row from the first write
    series_rows = (await db_session.execute(select(MrpDemandSeries))).scalars().all()
    assert len(series_rows) == 1 and series_rows[0].qty == Decimal("100")
    logs = (await db_session.execute(select(MrpForecastChangeLog))).scalars().all()
    assert len(logs) == 1


@pytest.mark.anyio
async def test_batch_with_one_past_month_cell_rejects_whole_batch_atomically(db_session):
    """A valid cell riding in the same batch as a past-month cell must NOT be
    written -- validation runs over the whole batch before any mutation."""
    with pytest.raises(PastMonthError):
        await upsert_cells(
            db_session,
            [
                CellChange("S0093", "2026-11", Decimal("100")),  # would be valid alone
                CellChange("S0093", "2026-08", Decimal("5")),  # current_month=2026-09 -> past
            ],
            current_month="2026-09", changed_by=None,
        )
    await db_session.commit()
    # nothing from the batch was written, not even the valid cell
    assert (await db_session.execute(select(MrpDemandSeries))).scalars().all() == []
    assert (await db_session.execute(select(MrpForecastChangeLog))).scalars().all() == []


@pytest.mark.anyio
async def test_batch_with_one_non_kg_cell_rejects_whole_batch_atomically(db_session):
    with pytest.raises(UomError):
        await upsert_cells(
            db_session,
            [
                CellChange("S0093", "2026-11", Decimal("100")),  # would be valid alone
                CellChange("S0060", "2026-11", Decimal("5"), uom="EA"),
            ],
            current_month="2026-09", changed_by=None,
        )
    await db_session.commit()
    assert (await db_session.execute(select(MrpDemandSeries))).scalars().all() == []
    assert (await db_session.execute(select(MrpForecastChangeLog))).scalars().all() == []


@pytest.mark.anyio
async def test_malformed_month_rejected_even_though_it_would_string_compare_as_future(db_session):
    """`upsert_cells`' past-month guard is a plain string comparison —
    "2026-1" > "2026-08" character-by-character (at index 6, '1' > '0'),
    so a malformed one-digit month would sail past `cell.month <
    current_month` and land in a real historical row if this shape guard
    didn't reject it first. Proves the belt-and-suspenders check in
    upsert_cells closes that bypass independent of the HTTP layer's own
    SeriesCellUpsert.month field_validator."""
    assert "2026-1" > "2026-08"  # sanity: confirms the string-compare bypass this guards against
    with pytest.raises(PastMonthError):
        await upsert_cells(
            db_session, [CellChange("S0093", "2026-1", Decimal("100"))],
            current_month="2026-08", changed_by=None,
        )
    await db_session.commit()
    assert (await db_session.execute(select(MrpDemandSeries))).scalars().all() == []
    assert (await db_session.execute(select(MrpForecastChangeLog))).scalars().all() == []


@pytest.mark.anyio
async def test_current_month_defaults_when_omitted(db_session):
    """current_month isn't required -- it defaults to today's real month, so
    a far-future cell must still succeed with no explicit current_month."""
    res = await upsert_cells(
        db_session, [CellChange("S0093", "2099-01", Decimal("1"))], changed_by=None,
    )
    assert (res.upserted, res.changed) == (1, 1)


# ── read_series_grid ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_read_series_grid_shape_and_totals(db_session):
    await upsert_cells(
        db_session,
        [
            CellChange("S0093", "2026-09", Decimal("100")),
            CellChange("S0093", "2026-10", Decimal("50")),
            CellChange("S0060", "2026-09", Decimal("20")),
        ],
        current_month="2026-09", changed_by=None,
    )
    # outside the requested range -- must not appear in months/cells/totals
    await upsert_cells(
        db_session, [CellChange("S0093", "2026-12", Decimal("999"))],
        current_month="2026-09", changed_by=None,
    )

    grid = await read_series_grid(db_session, "2026-09", "2026-10")

    assert grid["months"] == ["2026-09", "2026-10"]
    rows_by_code = {r["material_code"]: r for r in grid["rows"]}
    assert set(rows_by_code) == {"S0093", "S0060"}

    s0093 = rows_by_code["S0093"]
    assert s0093["cells"] == {"2026-09": Decimal("100"), "2026-10": Decimal("50")}
    assert s0093["total"] == Decimal("150")

    s0060 = rows_by_code["S0060"]
    assert s0060["cells"] == {"2026-09": Decimal("20"), "2026-10": Decimal("0")}
    assert s0060["total"] == Decimal("20")

    assert grid["column_totals"] == {"2026-09": Decimal("120"), "2026-10": Decimal("50")}
    assert grid["grand_total"] == Decimal("170")


@pytest.mark.anyio
async def test_read_series_grid_empty_range_returns_empty_grid(db_session):
    grid = await read_series_grid(db_session, "2030-01", "2030-01")
    assert grid == {
        "months": ["2030-01"],
        "rows": [],
        "column_totals": {"2030-01": Decimal("0")},
        "grand_total": Decimal("0"),
    }


# ── API: /series (Task 3) ────────────────────────────────────────────────
#
# Uses `client`/`admin_token`/`non_admin_token` (tests/conftest.py) — same
# HTTP-level harness `test_consignment.py`/`test_permission_gates.py` use.
# `current_month` is intentionally NOT under test control here — the
# endpoint resolves it from real wall-clock UTC (module docstring), so
# these tests write far-future months (2099-*) that can never be "past"
# regardless of when the suite runs, and separately assert a real past
# month (2020-01) 422s.


def _deny_everything(monkeypatch):
    """Same idiom as tests/test_permission_gates.py's helper of the same
    name (duplicated here rather than imported — that module doesn't
    export it for cross-file reuse): makes every role's effective
    permission set empty, so `non_admin_token` actually hits the 403 path
    instead of the system_admin fast path `admin_token` always takes."""
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)


@pytest.mark.anyio
async def test_put_cells_upserts_and_returns_counts(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.put(
        "/api/v1/series/cells",
        json={"cells": [
            {"material_code": "S0093", "month": "2099-01", "qty": "100"},
            {"material_code": "S0060", "month": "2099-01", "qty": "20"},
        ]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"upserted": 2, "changed": 2}


@pytest.mark.anyio
async def test_put_cells_past_month_returns_422(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": "2020-01", "qty": "5"}]},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "2020-01" in r.json()["detail"]


@pytest.mark.anyio
async def test_put_cells_non_kg_returns_422(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": "2099-01", "qty": "5", "uom": "EA"}]},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "EA" in r.json()["detail"]


@pytest.mark.anyio
@pytest.mark.parametrize("bad_month", ["2026-1", "2026-13", "abc", "2026-011"])
async def test_put_cells_malformed_body_month_returns_422(client, admin_token, bad_month):
    """SeriesCellUpsert.month's field_validator (app/api/v1/series.py) must
    422 a malformed body month via Pydantic's own validation, BEFORE it ever
    reaches upsert_cells' string-compare past-month guard — closes the
    "2026-1" > "2026-08" bypass that would otherwise defeat the
    past-read-only invariant, and "2026-011" would overflow the
    mrp_demand_series.month CHAR(7) column as an unhandled 500 rather than a
    clean 422 if it weren't rejected here."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": bad_month, "qty": "5"}]},
        headers=headers,
    )
    assert r.status_code == 422, r.text


@pytest.mark.anyio
async def test_put_cells_duplicate_key_in_one_batch_last_value_wins(client, admin_token):
    """A grid editor can emit the same (material_code, month) twice in one
    PUT batch (e.g. two edits to the same cell before the user saves). The
    service dedupes within-batch (app/services/demand_series.py's
    existing_by_key), but this asserts that contract survives the HTTP
    layer end to end: the final stored qty is the LAST cell's value, and
    the change log records exactly the two sequential transitions
    (None->10, then 10->20) — not a lost update and not a stray extra row."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.put(
        "/api/v1/series/cells",
        json={"cells": [
            {"material_code": "S0093", "month": "2099-02", "qty": "10"},
            {"material_code": "S0093", "month": "2099-02", "qty": "20"},
        ]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    # two writes happened (both cells triggered a real change), but they
    # collapse onto the same underlying row.
    assert r.json() == {"upserted": 2, "changed": 2}

    grid = (await client.get(
        "/api/v1/series", params={"from": "2099-02", "to": "2099-02"}, headers=headers,
    )).json()
    rows_by_code = {row["material_code"]: row for row in grid["rows"]}
    assert rows_by_code["S0093"]["cells"]["2099-02"] == "20.000"  # last value wins

    log = (await client.get(
        "/api/v1/series/change-log",
        params={"material_code": "S0093", "month": "2099-02"},
        headers=headers,
    )).json()
    entries = sorted(log["items"], key=lambda item: Decimal(item["new_qty"]))
    assert len(entries) == 2
    assert entries[0]["old_qty"] is None and entries[0]["new_qty"] == "10.000"
    assert entries[1]["old_qty"] == "10.000" and entries[1]["new_qty"] == "20.000"


@pytest.mark.anyio
async def test_get_series_returns_grid(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": "2099-03", "qty": "42"}]},
        headers=headers,
    )
    r = await client.get(
        "/api/v1/series", params={"from": "2099-03", "to": "2099-03"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["months"] == ["2099-03"]
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["material_code"] == "S0093"
    assert row["cells"] == {"2099-03": "42.000"}
    assert row["total"] == "42.000"
    assert body["column_totals"] == {"2099-03": "42.000"}
    assert body["grand_total"] == "42.000"


@pytest.mark.anyio
async def test_get_change_log_newest_first(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": "2099-04", "qty": "10"}]},
        headers=headers,
    )
    await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": "2099-04", "qty": "30"}]},
        headers=headers,
    )
    r = await client.get(
        "/api/v1/series/change-log", params={"material_code": "S0093"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2
    # newest first: the second PUT (10 -> 30) must come before the first (None -> 10)
    assert items[0]["old_qty"] == "10.000" and items[0]["new_qty"] == "30.000"
    assert items[1]["old_qty"] is None and items[1]["new_qty"] == "10.000"


# ── PUT /series/cells: changed_by_name (mrp06 follow-up) ────────────────────
#
# The change-history popover must show the editor's NAME, not their UUID.
# The endpoint resolves the caller's name ONCE per request via
# identity_client.resolve_current_user_name — imported into app/api/v1/series.py
# as a bare name (module docstring) so it's monkeypatchable here the same way
# tests/test_consignment.py monkeypatches `consignment.lookup_lot`.


@pytest.mark.anyio
async def test_put_cells_stores_and_returns_editor_name_from_identity_lookup(
    client, admin_token, monkeypatch,
):
    monkeypatch.setattr(series, "resolve_current_user_name", lambda token: "Jane Planner")
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": "2099-05", "qty": "7"}]},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    log = (await client.get(
        "/api/v1/series/change-log",
        params={"material_code": "S0093", "month": "2099-05"},
        headers=headers,
    )).json()
    assert len(log["items"]) == 1
    assert log["items"][0]["changed_by_name"] == "Jane Planner"


@pytest.mark.anyio
async def test_put_cells_saves_successfully_when_identity_lookup_returns_none(
    client, admin_token, monkeypatch,
):
    """identity-api down (or any lookup failure) degrades to changed_by_name
    =None -- resolve_current_user_name itself never raises (see its
    docstring), so this simulates its degraded return value directly. The
    save must still succeed -- a name lookup must never break a save."""
    monkeypatch.setattr(series, "resolve_current_user_name", lambda token: None)
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": "2099-06", "qty": "9"}]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"upserted": 1, "changed": 1}

    log = (await client.get(
        "/api/v1/series/change-log",
        params={"material_code": "S0093", "month": "2099-06"},
        headers=headers,
    )).json()
    assert len(log["items"]) == 1
    assert log["items"][0]["changed_by_name"] is None


@pytest.mark.anyio
async def test_put_cells_without_write_permission_returns_403(client, non_admin_token, monkeypatch):
    _deny_everything(monkeypatch)
    headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.put(
        "/api/v1/series/cells",
        json={"cells": [{"material_code": "S0093", "month": "2099-01", "qty": "5"}]},
        headers=headers,
    )
    assert r.status_code == 403
