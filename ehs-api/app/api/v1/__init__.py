from fastapi import APIRouter

from app.api.v1.actions import router as actions_router
from app.api.v1.assets import router as assets_router
from app.api.v1.courses import router as courses_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.deadlines import router as deadlines_router
from app.api.v1.documents import router as documents_router
from app.api.v1.equipment import router as equipment_router
from app.api.v1.first_aid import router as first_aid_router
from app.api.v1.forms import router as forms_router
from app.api.v1.health import router as health_router
from app.api.v1.incident_attachments import router as incident_attachments_router
from app.api.v1.incident_medical import router as incident_medical_router
from app.api.v1.incidents import router as incidents_router
from app.api.v1.jhsc import router as jhsc_router
from app.api.v1.qr import router as qr_router
from app.api.v1.reports import router as reports_router
from app.api.v1.risk import router as risk_router
from app.api.v1.settings import router as settings_router
from app.api.v1.toolbox import router as toolbox_router
from app.api.v1.training import router as training_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(incidents_router, prefix="/incidents", tags=["incidents"])
# Carries its own /incidents/{id}/attachments prefix.
api_router.include_router(incident_attachments_router, tags=["incidents"])
# Section B sits behind ehs.incident.medical.read, not incident read.
api_router.include_router(incident_medical_router, tags=["incidents"])
# /actions/mine is a static path and must be registered before /actions/{id},
# or "mine" is read as an action id.
api_router.include_router(actions_router, prefix="/actions", tags=["actions"])
api_router.include_router(settings_router, prefix="/settings", tags=["settings"])
api_router.include_router(risk_router, tags=["risk"])
api_router.include_router(equipment_router, tags=["equipment"])
api_router.include_router(reports_router, tags=["reports"])
api_router.include_router(dashboard_router, tags=["dashboard"])
api_router.include_router(training_router, prefix="/training", tags=["training"])
# Carries its own /course-sessions prefix. Registered after /training so the
# two read as one area in the generated docs.
api_router.include_router(courses_router, tags=["training"])
api_router.include_router(jhsc_router, tags=["jhsc"])
# /documents/mine is a static path and must precede /documents/{id}; it does,
# inside the router.
api_router.include_router(documents_router, tags=["documents"])
# Likewise /safety-assets/coverage and /safety-assets/inspections.
api_router.include_router(assets_router, tags=["assets"])
api_router.include_router(qr_router, tags=["qr"])
api_router.include_router(toolbox_router, tags=["toolbox"])
api_router.include_router(first_aid_router, prefix="/first-aid", tags=["first-aid"])
api_router.include_router(forms_router, tags=["forms"])
api_router.include_router(deadlines_router, prefix="/deadlines", tags=["statutory"])
