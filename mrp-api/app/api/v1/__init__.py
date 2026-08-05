from fastapi import APIRouter

from app.api.v1 import admin_sync, consignment, forecast, health, inventory, net_requirement

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(admin_sync.router)
api_router.include_router(inventory.router)
api_router.include_router(forecast.router)
api_router.include_router(consignment.router)
api_router.include_router(net_requirement.router)
