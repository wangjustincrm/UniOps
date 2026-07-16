"""/health must actually probe DB + Redis — not return a hardcoded ok.

2026-07-15 生产事故: 公司 MFA 一开,全员登录瘫痪(identity 连不上 Redis,
那台机器上根本没装 Redis)。排查时 identity 容器全程显示 `Up (healthy)`,
因为 /health 只返回写死的字典,DB/Redis 一个都不碰 —— 在任何依赖故障下都是
绿的,把排查方向直接带偏。

设计要点(见 app/api/v1/health.py 顶部注释):
- DB 挂 -> 503(identity 完全不可用,判 unhealthy 合理)。
- Redis 挂 -> 仍 200,但 body 里如实报告(只影响 MFA 登录,其余功能正常;
  判 unhealthy/触发重启会把局部故障放大成全服务宕机,比原事故更糟 —— 这条
  是本次任务最关键的行为)。
- 每个探测都有硬超时,防止某个挂起的依赖把 health 自己拖死。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import app.db.base as db_base
from app.api.v1 import health as health_module


async def test_health_ok_when_db_and_redis_up(client):
    """基线:DB ok + Redis ok -> 200,body 如实报告两者都 ok。"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["redis"] == "ok"


async def test_health_redis_down_is_degraded_but_still_200(client, monkeypatch):
    """★ 最关键的一条: Redis 挂只影响 MFA,不该把整个服务判死。

    局部故障(Redis)不能被放大成全服务 unhealthy/重启 —— 这正是今天事故
    应该避免重演的地方。
    """
    monkeypatch.setattr(
        health_module, "_redis_client",
        AsyncMock(side_effect=ConnectionError("redis host has no redis installed")),
    )

    resp = await client.get("/health")

    assert resp.status_code == 200, "Redis 故障不得让 health 返回非 200"
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["db"] == "ok"
    assert body["redis"].startswith("fail:")
    assert "redis host has no redis installed" in body["redis"]


async def test_health_db_down_returns_503(client, monkeypatch):
    """DB 挂 -> identity 干不了任何事,503 合理。"""
    # AsyncSessionLocal() itself is a synchronous call (sessionmaker __call__) —
    # use MagicMock, not AsyncMock, so calling it raises immediately instead of
    # returning an unawaited coroutine.
    monkeypatch.setattr(
        db_base, "AsyncSessionLocal",
        MagicMock(side_effect=RuntimeError("could not connect to server")),
    )

    resp = await client.get("/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error"
    assert body["db"].startswith("fail:")
    assert "could not connect to server" in body["db"]


async def test_health_probe_timeout_does_not_hang_forever(client, monkeypatch):
    """硬要求: 每个探测都要有短超时,否则一个挂起的依赖会拖死 health 自己 —
    那就正好复现了今天的故障模式(health 本身因为等一个没响应的连接而挂起)。
    """
    monkeypatch.setattr(health_module, "PROBE_TIMEOUT_SECONDS", 0.1)

    async def _hang_forever(*args, **kwargs):
        await asyncio.sleep(100)
        return "unreachable"

    monkeypatch.setattr(health_module, "_redis_client", AsyncMock(side_effect=_hang_forever))

    resp = await asyncio.wait_for(client.get("/health"), timeout=5.0)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert "timed out" in body["redis"]
