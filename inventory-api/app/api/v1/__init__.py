from fastapi import APIRouter

from app.api.v1 import admin_sync, health, nc_inventory, recon, settings, trace

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(admin_sync.router)
api_router.include_router(settings.router)
api_router.include_router(nc_inventory.router)
api_router.include_router(recon.router)
api_router.include_router(trace.router)
