from fastapi import APIRouter

from app.api.v1 import (
    admin, audit, badge, dashboard, health, health_decl, reports,
    visitors, visits,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(visitors.router)
api_router.include_router(visits.router)
api_router.include_router(badge.visit_badge_router)
api_router.include_router(badge.template_router)
api_router.include_router(dashboard.router)
api_router.include_router(audit.router)
api_router.include_router(health_decl.visit_health_router)
api_router.include_router(health_decl.template_router)
api_router.include_router(health_decl.declarations_router)
api_router.include_router(admin.router)
api_router.include_router(reports.router)
