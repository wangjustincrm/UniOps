from fastapi import APIRouter
from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok", "service": settings.SERVICE_NAME, "storage": str(settings.STORAGE_ROOT)}
