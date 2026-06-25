from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.core.config import settings
from app.db.base import AsyncSessionLocal

router = APIRouter(tags=["Health"])


async def _get_db():
    async with AsyncSessionLocal() as s:
        yield s


@router.get("/health")
async def health():
    return {"status": "ok", "service": settings.service_name, "version": settings.app_version}


@router.get("/health/db")
async def health_db(response: Response, db: AsyncSession = Depends(_get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected"}
    except Exception as exc:
        response.status_code = 503
        return {"status": "error", "db": "unreachable", "detail": str(exc)}
