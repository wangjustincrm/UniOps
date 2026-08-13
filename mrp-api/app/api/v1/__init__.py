from fastapi import APIRouter

from app.api.v1 import (
    admin_sync, capacity, consignment, forecast, health, intent, inventory, mps,
    net_requirement, params, series,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(admin_sync.router)
api_router.include_router(inventory.router)
api_router.include_router(forecast.router)
api_router.include_router(consignment.router)
api_router.include_router(net_requirement.router)
api_router.include_router(capacity.router)
api_router.include_router(mps.router)
api_router.include_router(series.router)
api_router.include_router(intent.router)
api_router.include_router(params.router)
