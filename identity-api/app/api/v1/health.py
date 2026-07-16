"""Health check — must actually probe dependencies, not return a hardcoded ok.

2026-07-15 生产事故: 公司 MFA 一开,全员登录瘫痪(identity 连不上 Redis,那台
机器上根本没装 Redis,缺席 33 天没人发现)。排查时 identity 容器全程显示
`Up (healthy)`,因为这里以前只 `return {"status": "ok", ...}`,DB/Redis 一个
都不碰 —— 在任何依赖故障下都是绿的,把排查方向直接带偏。一个在真故障时永远
绿的健康检查,比没有更糟。

设计(不要改动而不读这段):
- DB 挂 -> 503。identity 完全依赖 DB(用户/角色/审计全在里面),DB 不通就是
  真的 unhealthy,compose 判它死、甚至重启,都合理。
- Redis 挂 -> 仍然 200,但 body 里如实报告。Redis 只用于 OTP/refresh 黑名单,
  只影响 MFA 登录这一条路径,其余功能(非 MFA 登录、鉴权、审计等)完全正常。
  如果 Redis 故障被判成 unhealthy/触发重启,就是把一个局部故障放大成整个
  identity 服务被杀 —— 比今天的事故还糟。所以 Redis 探测失败只降级为
  "degraded",绝不改变 HTTP 状态码。
- 每个探测都套 PROBE_TIMEOUT_SECONDS 硬超时。否则一个挂起但没有明确报错的
  依赖(例如防火墙静默丢包,而不是干脆拒绝连接)会把 health 自己拖死 ——
  那正好是今天故障的翻版:上层永远等不到一个结果。
"""
import asyncio

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.db.redis import get_redis

router = APIRouter(tags=["health"])

PROBE_TIMEOUT_SECONDS = 2.0


async def _redis_client():
    """取共享的 redis 连接池对象(get_redis 是单例 async-generator 依赖)。"""
    async for client in get_redis():
        return client
    raise RuntimeError("get_redis() yielded nothing")


async def _probe_db() -> str:
    """探测数据库连通性。失败直接抛异常,由 _run_probe 统一处理超时/降级。"""
    from app.db.base import AsyncSessionLocal  # 延迟导入,便于测试打桩(见 test_health.py)

    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))
    return "ok"


async def _probe_redis() -> str:
    client = await _redis_client()
    await client.ping()
    return "ok"


async def _run_probe(coro) -> tuple[bool, str]:
    try:
        detail = await asyncio.wait_for(coro, timeout=PROBE_TIMEOUT_SECONDS)
        return True, detail
    except asyncio.TimeoutError:
        return False, f"fail: timed out after {PROBE_TIMEOUT_SECONDS}s"
    except Exception as exc:
        return False, f"fail: {exc}"


@router.get("/health")
async def health(response: Response) -> dict:
    # 并发探测,而不是串行 —— 避免 worst case(两个依赖都超时)把总耗时翻倍到
    # 逼近甚至超过 compose healthcheck 的 CMD timeout(见 docker-compose.prod.yml
    # identity-api healthcheck: timeout 5s)。
    (db_ok, db_detail), (redis_ok, redis_detail) = await asyncio.gather(
        _run_probe(_probe_db()), _run_probe(_probe_redis())
    )

    body = {
        "service": "identity-api",
        "db": db_detail,
        "redis": redis_detail,
    }

    if not db_ok:
        response.status_code = 503
        body["status"] = "error"
    elif not redis_ok:
        # Redis 故障只影响 MFA 登录,不放大成整个服务被判死 —— 见文件顶部说明。
        body["status"] = "degraded"
    else:
        body["status"] = "ok"

    return body
