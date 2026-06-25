"""Health check endpoints."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import RedisDep
from app.db.session import get_session

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """Basic liveness probe."""
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }


@router.get("/health/db")
async def health_db(response: Response, session: AsyncSession = Depends(get_session)):
    """Readiness probe — verifies PostgreSQL connectivity."""
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected"}
    except Exception as exc:
        response.status_code = 503
        return {"status": "error", "db": "unreachable", "detail": str(exc)}


@router.get("/health/redis")
async def health_redis(response: Response, redis: RedisDep):
    """Readiness probe — verifies Redis connectivity."""
    try:
        await redis.ping()
        return {"status": "ok", "redis": "connected"}
    except Exception as exc:
        response.status_code = 503
        return {"status": "error", "redis": "unreachable", "detail": str(exc)}
