from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.admin_rooms import router as admin_rooms_router
from app.api.v1.admin_config import router as admin_config_router
from app.api.v1.rooms import router as rooms_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(admin_rooms_router, prefix="/admin/rooms", tags=["admin"])
api_router.include_router(admin_config_router, prefix="/admin/config", tags=["admin"])
api_router.include_router(rooms_router, prefix="/rooms", tags=["rooms"])
