"""app.core.background: fire-and-forget tasks must be tracked and must release
their DB connection when the loop stops.

The bug this locks down: `asyncio.create_task(...)` with the Task thrown away.
Nothing referenced it (so it could be GC'd mid-flight — "Task was destroyed but
it is pending!") and nothing drained it at shutdown, so a coroutine suspended
inside `async with AsyncSessionLocal() as db` never reached `__aexit__`. Its
connection returned to the pool still inside a transaction, holding locks; a
later `DROP TABLE` teardown then blocked on it and wedged the suite.
"""
import asyncio

import pytest
from sqlalchemy import text

import app.db.session as sm
from app.core.background import _BACKGROUND, drain, pending_count, spawn


@pytest.mark.asyncio
async def test_spawn_keeps_a_strong_reference_until_done():
    started = asyncio.Event()
    release = asyncio.Event()

    async def work():
        started.set()
        await release.wait()

    task = spawn(work(), name="unit-test")
    await started.wait()
    assert task in _BACKGROUND, "任务没被强引用住,可能被 GC 中途销毁"
    assert pending_count() == 1

    release.set()
    await task
    # done-callback 自己摘除,注册表不会无限增长
    assert task not in _BACKGROUND
    assert pending_count() == 0


@pytest.mark.asyncio
async def test_drain_releases_a_session_left_open_mid_transaction():
    """核心回归:被 drain 取消的后台任务必须真的把连接还回去。

    直接观测 Postgres 侧的 `idle in transaction` 后端数 —— 这是当初卡死套件的
    那个状态,只断言「没抛异常」是查不出来的。
    """
    async def count_idle_in_transaction() -> int:
        async with sm.AsyncSessionLocal() as probe:
            return (await probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND state = 'idle in transaction'"
            ))).scalar_one()

    before = await count_idle_in_transaction()

    inside = asyncio.Event()
    never = asyncio.Event()

    async def leaky():
        # 正是生产 fire-and-forget 协程的形状:自己开 session、发一条查询,
        # 然后停在 await 上(现实里是 SMTP 往返)。
        async with sm.AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
            inside.set()
            await never.wait()          # 永远等不到 —— 模拟被事件循环丢下

    spawn(leaky(), name="leaky")
    await inside.wait()
    assert await count_idle_in_transaction() == before + 1, "前提没成立:连接并未处于事务中"

    assert await drain(timeout=0) == 1
    assert pending_count() == 0
    assert await count_idle_in_transaction() == before, "drain 之后连接仍卡在事务里"


@pytest.mark.asyncio
async def test_drain_is_safe_when_nothing_is_pending():
    assert await drain(timeout=0) == 0
    assert await drain(timeout=0) == 0


@pytest.mark.asyncio
async def test_spawn_without_a_running_loop_does_not_warn():
    """没有事件循环时丢弃任务,但要把协程 close 掉,否则 Python 会抛
    "coroutine was never awaited" 的 RuntimeWarning —— 全量日志里出现过。"""
    async def work():
        pass

    coro = work()

    def sync_caller():
        return spawn(coro, name="no-loop")

    # 在独立线程里跑,那里没有 running loop
    import threading
    result = {}
    t = threading.Thread(target=lambda: result.update(task=sync_caller()))
    t.start()
    t.join()
    assert result["task"] is None
    assert coro.cr_frame is None, "协程没有被 close,会触发 never-awaited 警告"
