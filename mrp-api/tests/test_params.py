"""Planning parameters (key-value settings) — GET/PUT over `mrp_planning_params`.

`week_calendar_mode` is the only writable key as of Task 2 (Phase 1C's
loss-rate parameters land in the same table later — see
app/models/params.py's docstring). Round-trip + default + unknown-mode
rejection + the write-permission gate.

Also covers `mrp_capacity_exceptions`'s partial unique index (fix round 1)
against raw SQL, not an ORM model — Task 3 owns that table's ORM model and
must not create it again here.
"""
from datetime import date

import pytest
from sqlalchemy import text


def _deny_everything(monkeypatch):
    """Copied from tests/test_permission_gates.py's helper of the same name
    (brief explicitly allows import-or-copy) — this DB has none of
    identity's role_permissions/role_defs/user_roles tables, so a real
    require_permission(...) gate hit with `non_admin_token` would 500 on a
    missing table rather than 403 without this monkeypatch. `admin_token`
    always short-circuits via the system_admin fast path and would prove
    nothing about the actual permission key."""
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)


@pytest.mark.asyncio
async def test_week_calendar_mode_defaults_to_iso_thursday(client, auth_headers):
    r = await client.get("/api/v1/params", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["week_calendar_mode"] == "iso_thursday"


@pytest.mark.asyncio
async def test_week_calendar_mode_round_trips(client, auth_headers):
    r = await client.put("/api/v1/params/week_calendar_mode",
                         json={"value": "month_fixed"}, headers=auth_headers)
    assert r.status_code == 200
    assert (await client.get("/api/v1/params",
                             headers=auth_headers)).json()["week_calendar_mode"] == "month_fixed"


@pytest.mark.asyncio
async def test_unknown_week_mode_is_rejected(client, auth_headers):
    r = await client.put("/api/v1/params/week_calendar_mode",
                         json={"value": "fiscal_445"}, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_params_write_requires_param_write_permission(client, non_admin_token, monkeypatch):
    # non_admin_token + _deny_everything, not admin_token — see module
    # docstring / helper docstring above.
    _deny_everything(monkeypatch)
    r = await client.put("/api/v1/params/week_calendar_mode", json={"value": "month_fixed"},
                         headers={"Authorization": f"Bearer {non_admin_token}"})
    assert r.status_code == 403


# ── mrp_capacity_exceptions: partial unique index (fix round 1) ────────────
#
# No ORM model exists for this table yet (Task 3 adds it), so these insert
# raw SQL directly against db_session.

_INSERT_EXCEPTION = text(
    "INSERT INTO mrp_capacity_exceptions "
    "(week_start, scope_type, scope_ref, constraint_type, limit_value) "
    "VALUES (:week_start, :scope_type, :scope_ref, :constraint_type, :limit_value)"
)

_WEEK = date(2026, 8, 10)


async def _insert_exception(db_session, **overrides):
    params = {
        "week_start": _WEEK, "scope_type": "factory", "scope_ref": None,
        "constraint_type": "max_output_qty", "limit_value": 10,
    }
    params.update(overrides)
    await db_session.execute(_INSERT_EXCEPTION, params)


@pytest.mark.asyncio
async def test_capacity_exception_duplicate_factory_week_and_type_is_rejected(db_session):
    """uq_mrp_capacity_exceptions_factory_week_constraint. Postgres treats
    every NULL as distinct, so the plain 4-column unique constraint
    (week_start, scope_type, scope_ref, constraint_type) does not dedupe
    factory-wide rows (scope_ref IS NULL) — the only scope this phase
    uses. Without the partial index added in fix round 1, two
    contradictory factory-wide rows ("week 32 max output = 0" and "= 40")
    would both insert, and Task 3's resolver would pick one arbitrarily
    with no DB guarantee. This proves the partial index blocks it."""
    await _insert_exception(db_session, limit_value=0)
    await db_session.flush()

    # Raw SQL via db_session.execute() hits the DB immediately (unlike an
    # ORM db.add(), which only flushes at the next flush()), so the
    # constraint violation raises here, not on a later flush().
    with pytest.raises(Exception):
        await _insert_exception(db_session, limit_value=40)


@pytest.mark.asyncio
async def test_capacity_exception_allows_different_constraint_type_same_week(db_session):
    """Two factory-wide rows for the same week but different
    constraint_type must NOT collide — the partial index is scoped to
    (week_start, scope_type, constraint_type), not just (week_start,
    scope_type)."""
    await _insert_exception(db_session, constraint_type="max_output_qty")
    await _insert_exception(db_session, constraint_type="max_sku_count")
    await db_session.flush()  # must not raise

    count = (await db_session.execute(text(
        "SELECT count(*) FROM mrp_capacity_exceptions WHERE week_start = :w"
    ), {"w": _WEEK})).scalar()
    assert count == 2


@pytest.mark.asyncio
async def test_capacity_exception_allows_factory_and_scoped_row_same_week_and_type(db_session):
    """One factory-wide row (scope_ref NULL) and one scoped row (scope_ref
    non-null) for the same week + constraint_type must both succeed — the
    partial index only applies WHERE scope_ref IS NULL, and the plain
    4-column constraint already treats the two rows as distinct since
    their scope_ref differs."""
    await _insert_exception(db_session, scope_type="factory", scope_ref=None)
    await _insert_exception(db_session, scope_type="line", scope_ref="LINE-1")
    await db_session.flush()  # must not raise

    count = (await db_session.execute(text(
        "SELECT count(*) FROM mrp_capacity_exceptions WHERE week_start = :w"
    ), {"w": _WEEK})).scalar()
    assert count == 2


# ── week_start_dow（周起始日）+ 检修周平移 ────────────────────────────────
#
# 工厂的周是周六→周五。改这个设置会把整个周网格平移，而产能例外（检修周）是按
# week_start 日期存的 —— 不跟着平移，检修周就会**静默失效**。所以平移与参数写入
# 必须在同一个事务里，冲突时整体回滚。


@pytest.mark.asyncio
async def test_week_start_dow_defaults_to_monday(client, auth_headers):
    r = await client.get("/api/v1/params", headers=auth_headers)
    assert r.status_code == 200
    assert r.json().get("week_start_dow") in (None, 0)


@pytest.mark.asyncio
async def test_put_week_start_dow_accepts_saturday(client, auth_headers):
    r = await client.put("/api/v1/params/week_start_dow", json={"value": 5},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["week_start_dow"] == 5
    assert (await client.get("/api/v1/params", headers=auth_headers)).json()["week_start_dow"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [-1, 7, "sat", 1.5, None, True])
async def test_put_week_start_dow_rejects_bad_values(client, auth_headers, bad):
    r = await client.put("/api/v1/params/week_start_dow", json={"value": bad},
                         headers=auth_headers)
    assert r.status_code == 422, r.text


async def _make_exception(client, auth_headers, week_start: str):
    r = await client.post("/api/v1/capacity/exceptions", json={
        "week_start": week_start, "scope_type": "factory", "scope_ref": None,
        "constraint_type": "max_output_qty", "limit_value": 0, "uom": "KG",
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_changing_week_start_shifts_capacity_exceptions(client, auth_headers):
    # 检修周：周一制下的 2026-08-17（周一）
    await _make_exception(client, auth_headers, "2026-08-17")

    r = await client.put("/api/v1/params/week_start_dow", json={"value": 5},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["exceptions_shifted"] == 1

    rows = (await client.get("/api/v1/capacity/exceptions", headers=auth_headers)).json()
    # 8/17 落在周六起算的 8/15~8/21 那一周
    assert [x["week_start"] for x in rows] == ["2026-08-15"]


@pytest.mark.asyncio
async def test_exceptions_already_on_the_new_grid_are_left_alone(client, auth_headers):
    await _make_exception(client, auth_headers, "2026-08-15")     # 已经是周六
    r = await client.put("/api/v1/params/week_start_dow", json={"value": 5},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["exceptions_shifted"] == 0


@pytest.mark.asyncio
async def test_exception_shift_conflict_is_409_and_changes_nothing(client, auth_headers):
    # 周一制的 8/17(周一) 与 8/20(周四) 在周六制下都落进 8/15 那一周
    await _make_exception(client, auth_headers, "2026-08-17")
    await _make_exception(client, auth_headers, "2026-08-20")

    r = await client.put("/api/v1/params/week_start_dow", json={"value": 5},
                         headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "2026-08-15" in r.text

    # 整个请求回滚：参数没改，例外一条没动
    params = (await client.get("/api/v1/params", headers=auth_headers)).json()
    assert params.get("week_start_dow") in (None, 0)
    rows = (await client.get("/api/v1/capacity/exceptions", headers=auth_headers)).json()
    assert sorted(x["week_start"] for x in rows) == ["2026-08-17", "2026-08-20"]


@pytest.mark.asyncio
async def test_month_fixed_mode_shifts_nothing(client, auth_headers):
    """month_fixed 的周按 1/8/15/22/29 切，与周起始日无关 —— 一条都不该动。"""
    await client.put("/api/v1/params/week_calendar_mode", json={"value": "month_fixed"},
                     headers=auth_headers)
    await _make_exception(client, auth_headers, "2026-08-08")
    r = await client.put("/api/v1/params/week_start_dow", json={"value": 5},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["exceptions_shifted"] == 0
    rows = (await client.get("/api/v1/capacity/exceptions", headers=auth_headers)).json()
    assert [x["week_start"] for x in rows] == ["2026-08-08"]
