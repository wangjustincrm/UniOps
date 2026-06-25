from fastapi import APIRouter

from app.api.v1 import (
    actual, balance, catalog, crossservice, factor, health, hierarchy, plan, settings,
)

api_router = APIRouter()
api_router.include_router(catalog.router)
api_router.include_router(factor.router)
api_router.include_router(plan.router)
api_router.include_router(balance.router)
api_router.include_router(actual.router)
api_router.include_router(hierarchy.router)
api_router.include_router(crossservice.router)
api_router.include_router(settings.router)
api_router.include_router(health.router)
