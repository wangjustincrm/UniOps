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
