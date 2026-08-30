from fastapi import APIRouter

from app.api.v1.actions import router as actions_router
from app.api.v1.first_aid import router as first_aid_router
from app.api.v1.health import router as health_router
from app.api.v1.incidents import router as incidents_router
from app.api.v1.settings import router as settings_router
from app.api.v1.training import router as training_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(incidents_router, prefix="/incidents", tags=["incidents"])
# /actions/mine is a static path and must be registered before /actions/{id},
# or "mine" is read as an action id.
api_router.include_router(actions_router, prefix="/actions", tags=["actions"])
api_router.include_router(settings_router, prefix="/settings", tags=["settings"])
api_router.include_router(training_router, prefix="/training", tags=["training"])
api_router.include_router(first_aid_router, prefix="/first-aid", tags=["first-aid"])
