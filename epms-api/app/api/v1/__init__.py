from fastapi import APIRouter

from app.api.v1.agreement_attachments import router as agreement_attachments_router
from app.api.v1.agreements import router as agreements_router
from app.api.v1.auth import router as auth_router
from app.api.v1.config import router as config_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.reports import router as reports_router
from app.api.v1.admin import router as admin_router
# NOTE: budget router moved to budget-api (:8007). Frontend now calls budget-api directly.
from app.api.v1.cost_centers import router as cost_centers_router
from app.api.v1.departments import router as departments_router
from app.api.v1.gr import router as gr_router
from app.api.v1.gr_attachments import router as gr_attachments_router
from app.api.v1.health import router as health_router
from app.api.v1.invoices import router as invoices_router
from app.api.v1.invoice_tax import router as invoice_tax_router
from app.api.v1.nc_purchase_sync import router as nc_purchase_sync_router
from app.api.v1.pa import router as pa_router
from app.api.v1.pa_attachments import router as pa_attachments_router
from app.api.v1.parts import router as parts_router
from app.api.v1.pms_import import router as pms_import_router
from app.api.v1.po import router as po_router
from app.api.v1.po_attachments import router as po_attachments_router
from app.api.v1.pr import router as pr_router
from app.api.v1.pr_attachments import router as pr_attachments_router
from app.api.v1.projects import router as projects_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.users import router as users_router
from app.api.v1.vendors import router as vendors_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(departments_router)
api_router.include_router(cost_centers_router)
api_router.include_router(users_router)
api_router.include_router(parts_router)
api_router.include_router(vendors_router)
api_router.include_router(projects_router)
api_router.include_router(pr_router)
api_router.include_router(pr_attachments_router)
api_router.include_router(po_router)
api_router.include_router(po_attachments_router)
api_router.include_router(gr_router)
api_router.include_router(gr_attachments_router)
api_router.include_router(invoices_router)
api_router.include_router(invoice_tax_router)
api_router.include_router(pa_router)
api_router.include_router(pa_attachments_router)
api_router.include_router(agreements_router)
api_router.include_router(agreement_attachments_router)
api_router.include_router(tasks_router)
api_router.include_router(config_router)
api_router.include_router(pms_import_router)
api_router.include_router(dashboard_router)
api_router.include_router(reports_router)
# nc_purchase_sync_router (prefix /admin/nc-purchase-sync) MUST be registered
# before admin_router — admin_router's catch-all /admin/{entity}/{record_id}
# and /admin/{entity} routes would otherwise shadow it (FastAPI matches
# routers in registration order).
api_router.include_router(nc_purchase_sync_router)
api_router.include_router(admin_router)
