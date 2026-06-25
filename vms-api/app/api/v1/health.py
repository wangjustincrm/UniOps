"""Health check endpoints."""
from fastapi import APIRouter
from sqlalchemy import text

from app.core.deps import SessionDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "vms-api"}


@router.get("/health/db")
async def health_db(db: SessionDep) -> dict:
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "reachable"}
