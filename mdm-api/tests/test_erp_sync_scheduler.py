"""ERP 主数据同步调度器 —— 纯决策函数 + 三小类顺序执行。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.tasks import erp_sync_scheduler as sched

NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


def test_null_resolves_to_default():
    assert sched.resolve_interval_minutes(None) == sched.DEFAULT_INTERVAL_MINUTES


def test_zero_means_off():
    assert sched.resolve_interval_minutes(0) == 0


def test_negative_clamps_to_zero():
    assert sched.resolve_interval_minutes(-1) == 0


def test_above_max_clamps_to_max():
    assert sched.resolve_interval_minutes(99999) == sched.MAX_INTERVAL_MINUTES


def test_bool_is_not_an_integer():
    assert sched.resolve_interval_minutes(True) == sched.DEFAULT_INTERVAL_MINUTES


def test_never_run_is_due():
    assert sched.is_due(last_started_at=None, interval_minutes=1440, now=NOW) is True


def test_disabled_is_never_due():
    assert sched.is_due(last_started_at=None, interval_minutes=0, now=NOW) is False


def test_not_due_before_interval_elapses():
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=1439),
                        interval_minutes=1440, now=NOW) is False


def test_due_exactly_at_interval():
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=1440),
                        interval_minutes=1440, now=NOW) is True


def test_future_start_is_not_due():
    assert sched.is_due(last_started_at=NOW + timedelta(minutes=5),
                        interval_minutes=1440, now=NOW) is False


@pytest.mark.asyncio
async def test_run_all_kinds_runs_three_in_order(monkeypatch):
    called = []

    async def _fake(db, kind, **kw):
        called.append(kind)
        return {"total": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(sched, "sync_kind", _fake)
    await sched.run_all_kinds(db=None)
    assert called == ["material", "supplier", "person"]


@pytest.mark.asyncio
async def test_run_all_kinds_never_asks_for_full(monkeypatch):
    """自动跑必须永远是增量 —— full 会把整个 ERP 从头拉一遍。"""
    seen = []

    async def _fake(db, kind, **kw):
        seen.append(kw.get("full"))
        return {"total": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(sched, "sync_kind", _fake)
    await sched.run_all_kinds(db=None)
    assert seen == [False, False, False]


@pytest.mark.asyncio
async def test_one_failing_kind_does_not_block_the_others(monkeypatch):
    """供应商和人员的数据与物料无关 —— 一类的接口抖动不该废掉整轮。"""
    called = []

    async def _fake(db, kind, **kw):
        called.append(kind)
        if kind == "material":
            raise RuntimeError("ERP down")
        return {"total": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(sched, "sync_kind", _fake)
    await sched.run_all_kinds(db=None)
    assert called == ["material", "supplier", "person"]


@pytest.mark.asyncio
async def test_failing_kind_rolls_back_shared_session_before_next_kind(monkeypatch):
    """三个小类共用一个 session。一类抛错后不 rollback,下一类的 autoflush
    会在上一类留下的脏状态上炸 —— 变成互不相关的两类一起失败,正是这个
    任务要求禁止的"一类抖动废了整轮"。这个测试用一个记录调用的假 db,
    在 rollback 缺失时会失败(因为断言的调用顺序里没有 'rollback')。"""
    calls = []

    class FakeDb:
        async def rollback(self):
            calls.append("rollback")

    async def _fake(db, kind, **kw):
        calls.append(kind)
        if kind == "material":
            raise RuntimeError("dirty session left behind by material sync")
        return {"total": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(sched, "sync_kind", _fake)
    await sched.run_all_kinds(db=FakeDb())
    assert calls == ["material", "rollback", "supplier", "person"]


@pytest.mark.asyncio
async def test_run_tick_disabled(monkeypatch):
    async def _interval():
        return 0
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    assert await sched.run_tick() == "disabled"


@pytest.mark.asyncio
async def test_run_tick_not_due(monkeypatch):
    async def _interval():
        return 1440

    async def _last():
        # run_tick() 内部用真实 datetime.now(),不是这份测试的 NOW 常量 ——
        # 所以"刚跑过"必须相对真实时钟表达。写成 `return NOW` 会让这条用例
        # 在 NOW + interval 之后开始假失败(本例 interval=1440,即写下它的
        # 第二天就翻车 —— 实际发生过)。写法对齐 finance-api 的同名用例。
        return datetime.now(timezone.utc) - timedelta(minutes=5)
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_sync_started_at", _last)
    assert await sched.run_tick() == "not_due"


@pytest.mark.asyncio
async def test_run_tick_synced(monkeypatch):
    async def _interval():
        return 1440

    async def _last():
        return None

    async def _run():
        return None
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_sync_started_at", _last)
    # patch 的是开 session 的那层包装,不是 run_all_kinds 本身 ——
    # 否则 run_tick 仍会去开一个真实 session(而且用的是 settings.database_url,
    # 不是测试库)。
    monkeypatch.setattr(sched, "run_all_kinds_session", _run)
    assert await sched.run_tick() == "synced"
