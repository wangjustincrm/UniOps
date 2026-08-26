"""JV 同步调度器 —— 纯决策函数的测试,不碰 NC、不碰时钟。

形状对齐 epms-api/tests/test_nc_purchase_sync_scheduler.py。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.tasks import nc_sync_scheduler as sched

NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


# ── resolve_interval_minutes ─────────────────────────────────────────────
def test_null_resolves_to_default():
    assert sched.resolve_interval_minutes(None) == sched.DEFAULT_INTERVAL_MINUTES


def test_zero_means_off():
    assert sched.resolve_interval_minutes(0) == 0


def test_negative_clamps_to_zero():
    assert sched.resolve_interval_minutes(-5) == 0


def test_above_max_clamps_to_max():
    assert sched.resolve_interval_minutes(99999) == sched.MAX_INTERVAL_MINUTES


def test_bool_is_not_an_integer():
    # True 在 Python 里 isinstance(True, int) 为真 —— 手改脏值不能被当成 1 分钟
    assert sched.resolve_interval_minutes(True) == sched.DEFAULT_INTERVAL_MINUTES


def test_non_integer_resolves_to_default():
    assert sched.resolve_interval_minutes("60") == sched.DEFAULT_INTERVAL_MINUTES


def test_ordinary_value_passes_through():
    assert sched.resolve_interval_minutes(30) == 30


# ── is_due ───────────────────────────────────────────────────────────────
def test_never_run_is_due():
    assert sched.is_due(last_started_at=None, interval_minutes=60, now=NOW) is True


def test_disabled_is_never_due():
    assert sched.is_due(last_started_at=None, interval_minutes=0, now=NOW) is False


def test_not_due_before_interval_elapses():
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=59),
                        interval_minutes=60, now=NOW) is False


def test_due_exactly_at_interval():
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=60),
                        interval_minutes=60, now=NOW) is True


def test_naive_timestamp_treated_as_utc():
    naive = (NOW - timedelta(minutes=61)).replace(tzinfo=None)
    assert sched.is_due(last_started_at=naive, interval_minutes=60, now=NOW) is True


def test_future_start_is_not_due():
    # 时钟回拨:多等一个 interval 无害,当成逾期会连续同步
    assert sched.is_due(last_started_at=NOW + timedelta(minutes=5),
                        interval_minutes=60, now=NOW) is False


# ── run_tick ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_tick_disabled_when_interval_zero(monkeypatch):
    async def _interval():
        return 0
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    assert await sched.run_tick() == "disabled"


@pytest.mark.asyncio
async def test_run_tick_not_configured(monkeypatch):
    async def _interval():
        return 60
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: False)
    assert await sched.run_tick() == "not_configured"


@pytest.mark.asyncio
async def test_run_tick_not_due(monkeypatch):
    async def _interval():
        return 60

    async def _last():
        # run_tick() 内部用真实 datetime.now(),不是这份测试的 NOW 常量 ——
        # 所以"刚跑过"必须相对真实时钟表达,否则测试只在写下它的那一刻附近
        # 不到点、几小时后就假失败(照抄
        # epms-api/tests/test_nc_purchase_sync_scheduler.py::test_tick_waits_out_the_interval
        # 的写法)。
        return datetime.now(timezone.utc) - timedelta(minutes=5)
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    assert await sched.run_tick() == "not_due"


@pytest.mark.asyncio
async def test_run_tick_already_running(monkeypatch):
    async def _interval():
        return 60

    async def _last():
        return None
    def _boom(*a, **kw):
        raise sched.svc.SyncAlreadyRunning("busy")
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    monkeypatch.setattr(sched.svc, "start_run", _boom)
    assert await sched.run_tick() == "already_running"


@pytest.mark.asyncio
async def test_run_tick_synced(monkeypatch):
    import uuid
    async def _interval():
        return 60

    async def _last():
        return None
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    monkeypatch.setattr(sched.svc, "start_run", lambda *a, **kw: uuid.uuid4())
    assert await sched.run_tick() == "synced"


@pytest.mark.asyncio
async def test_run_tick_failed(monkeypatch):
    async def _interval():
        return 60

    async def _last():
        return None
    def _boom(*a, **kw):
        raise RuntimeError("NC exploded")
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    monkeypatch.setattr(sched.svc, "start_run", _boom)
    assert await sched.run_tick() == "failed"


@pytest.mark.asyncio
async def test_run_tick_never_runs_full(monkeypatch):
    """自动跑必须永远是 incremental —— full 是删了重建。"""
    import uuid
    captured = {}
    async def _interval():
        return 60

    async def _last():
        return None
    def _capture(mode, started_by, **kw):
        captured["mode"] = mode
        captured["started_by"] = started_by
        return uuid.uuid4()
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    monkeypatch.setattr(sched.svc, "start_run", _capture)
    await sched.run_tick()
    assert captured["mode"] == "incremental"
    # 没人按按钮,不该把某个人的名字记到机器跑的这条上
    assert captured["started_by"] is None
