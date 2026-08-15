"""生产计划版本模型：全局恰好一个生效版，计划组只能往前走。

`mrp_demands` 是喂 1C 采购的**唯一现行需求集**。在这之前 `confirm-release`
只把自己置 `released`、不动前任，于是库里可以有多条同时 `released` 的 run，
「哪一版在生效」没有唯一答案 —— 锁定区只好用「最近一次 released」去猜。
本模块钉住新规则：`is_default` 全局单例（部分唯一索引兜底），发布新组会把所有
更早组置 `superseded`，跨组切换与旧组发布一律 422。
"""
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from app.models.mps import MrpMpsRun


def test_versioning_columns_exist_with_defaults():
    cols = MrpMpsRun.__table__.c
    assert cols.is_default.default.arg is False
    assert str(cols.is_default.server_default.arg) == "false"
    assert cols.released_at.nullable is True


def _run_row(run_no: str, *, month: str = "2026-09", default: bool = True) -> MrpMpsRun:
    return MrpMpsRun(
        run_no=run_no,
        forecast_version_id=uuid.uuid4(),
        horizon_start_month=month,
        horizon_months=18,
        status="released",
        safety_margin_fraction=0,
        is_default=default,
        released_at=datetime.now(timezone.utc),
    )


@pytest.mark.anyio
async def test_database_refuses_a_second_default(db_session):
    """★应用层的「先清旧的再置新的」在并发下不可靠，而这条不变量一旦破
    （两版同时生效），`mrp_demands` 的口径就说不清了 —— 交给部分唯一索引。"""
    db_session.add(_run_row("MPS-DUP-1"))
    await db_session.commit()

    db_session.add(_run_row("MPS-DUP-2"))
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.anyio
async def test_many_non_default_runs_are_fine(db_session):
    """部分唯一索引只约束 is_default=true 的行；非生效版要多少有多少。

    没有这条，一个写成普通唯一索引的实现也会让上面那条通过 —— 而它会把
    第二条非生效版也拒掉，整个功能都建不起来。"""
    db_session.add_all([
        _run_row("MPS-N-1", default=False),
        _run_row("MPS-N-2", default=False),
        _run_row("MPS-N-3", default=False),
    ])
    await db_session.commit()

    count = (await db_session.execute(
        sa.select(sa.func.count()).select_from(MrpMpsRun)
        .where(MrpMpsRun.is_default.is_(False))
    )).scalar_one()
    assert count == 3


# ── 发布规则 ─────────────────────────────────────────────────────────────
#
# 复用 test_mps_api.py 的夹具与工具：同一目录、同一 conftest。

from datetime import date, timedelta                                   # noqa: E402

from tests.test_mps_api import (                                       # noqa: E402
    _confirmed_version, _factory_rule, _future_month, _no_shelf_life,
)
from app.api.v1 import mps as mps_module                               # noqa: E402


async def _run_for(client, db_session, headers, *, start, qty="100"):
    version, _ = await _confirmed_version(db_session, start=start, months=2,
                                          monthly_qty=qty)
    r = await client.post(
        "/api/v1/mps/runs",
        json={"forecast_version_id": version["id"], "production_lead_weeks": 0},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _get(client, headers, run_id):
    r = await client.get(f"/api/v1/mps/runs/{run_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _demand_rows(db_session):
    from app.models.demand import MrpDemand
    rows = (await db_session.execute(sa.select(MrpDemand))).scalars().all()
    return sorted((r.material_code, r.plan_week_start, r.qty) for r in rows)


@pytest.mark.anyio
async def test_releasing_a_second_version_moves_the_default_and_keeps_the_first_released(
    client, db_session, admin_token, monkeypatch,
):
    """同组第二版发布后：新版生效，旧版仍 released（还能切回去），不是 superseded。"""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await _factory_rule(client, headers)
    month = _future_month(1)

    v1 = await _run_for(client, db_session, headers, start=month)
    assert (await client.post(f"/api/v1/mps/runs/{v1['id']}/confirm-release",
                              headers=headers)).status_code == 200
    v2 = await _run_for(client, db_session, headers, start=month, qty="150")
    assert (await client.post(f"/api/v1/mps/runs/{v2['id']}/confirm-release",
                              headers=headers)).status_code == 200

    after1, after2 = await _get(client, headers, v1["id"]), await _get(client, headers, v2["id"])
    assert (after1["status"], after1["is_default"]) == ("released", False)
    assert (after2["status"], after2["is_default"]) == ("released", True)


@pytest.mark.anyio
async def test_releasing_a_newer_group_supersedes_every_older_run(
    client, db_session, admin_token, monkeypatch,
):
    """旧组的 released 与 draft 一并 superseded —— 旧组从此不可能再生效。"""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await _factory_rule(client, headers)

    old = await _run_for(client, db_session, headers, start=_future_month(1))
    old_draft = await _run_for(client, db_session, headers, start=_future_month(1), qty="90")
    assert (await client.post(f"/api/v1/mps/runs/{old['id']}/confirm-release",
                              headers=headers)).status_code == 200

    new = await _run_for(client, db_session, headers, start=_future_month(2))
    assert (await client.post(f"/api/v1/mps/runs/{new['id']}/confirm-release",
                              headers=headers)).status_code == 200

    assert (await _get(client, headers, old["id"]))["status"] == "superseded"
    assert (await _get(client, headers, old_draft["id"]))["status"] == "superseded"
    assert (await _get(client, headers, new["id"]))["is_default"] is True


@pytest.mark.anyio
async def test_releasing_an_older_group_draft_is_refused(
    client, db_session, admin_token, monkeypatch,
):
    """★发布即生效，所以放行旧组发布 = 让生效组倒退，绕过「只能往前走」。"""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await _factory_rule(client, headers)

    old = await _run_for(client, db_session, headers, start=_future_month(1))
    new = await _run_for(client, db_session, headers, start=_future_month(2))
    assert (await client.post(f"/api/v1/mps/runs/{new['id']}/confirm-release",
                              headers=headers)).status_code == 200
    before = await _demand_rows(db_session)

    r = await client.post(f"/api/v1/mps/runs/{old['id']}/confirm-release", headers=headers)
    assert r.status_code == 422, r.text
    assert "only moves forward" in r.text
    # 旧组被新组接管后已是 superseded，且需求集一行未动
    assert (await _get(client, headers, old["id"]))["is_default"] is False
    assert await _demand_rows(db_session) == before


@pytest.mark.anyio
async def test_releasing_the_current_default_again_is_a_no_op(
    client, db_session, admin_token, monkeypatch,
):
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await _factory_rule(client, headers)

    run = await _run_for(client, db_session, headers, start=_future_month(1))
    assert (await client.post(f"/api/v1/mps/runs/{run['id']}/confirm-release",
                              headers=headers)).status_code == 200
    before = await _demand_rows(db_session)

    again = await client.post(f"/api/v1/mps/runs/{run['id']}/confirm-release", headers=headers)
    assert again.status_code == 200, again.text
    assert again.json()["is_default"] is True
    assert await _demand_rows(db_session) == before


@pytest.mark.anyio
async def test_the_first_release_needs_no_group_comparison(
    client, db_session, admin_token, monkeypatch,
):
    """全新库没有生效版：任何 draft 都能发布，并成为第一个生效版。"""
    monkeypatch.setattr(mps_module, "resolve_shelf_life", _no_shelf_life)
    headers = {"Authorization": f"Bearer {admin_token}"}
    await _factory_rule(client, headers)

    run = await _run_for(client, db_session, headers, start=_future_month(1))
    released = await client.post(f"/api/v1/mps/runs/{run['id']}/confirm-release",
                                 headers=headers)
    assert released.status_code == 200, released.text
    assert released.json()["is_default"] is True
    assert released.json()["released_at"] is not None
