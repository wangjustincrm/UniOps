from fastapi import APIRouter

from app.api.v1 import admin_sync, health, inventory

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(admin_sync.router)
api_router.include_router(inventory.router)
