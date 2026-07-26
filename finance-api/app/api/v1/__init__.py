from fastapi import APIRouter
from app.api.v1.budget import router as budget_router
from app.api.v1.ap import router as ap_router
from app.api.v1.ap_invoices import router as ap_invoices_router
from app.api.v1.ar import router as ar_router
from app.api.v1.payments import router as payments_router
from app.api.v1.posting import router as posting_router
from app.api.v1.periods import router as periods_router
from app.api.v1.coa import router as coa_router
from app.api.v1.bank import router as bank_router
from app.api.v1.taxreturn import router as taxreturn_router
from app.api.v1.gl import router as gl_router
from app.api.v1.journal_voucher import router as journal_voucher_router
from app.api.v1.account_balance import router as account_balance_router
from app.api.v1.admin import router as admin_router
from app.api.v1.health import router as health_router
from app.api.v1.nc_sync import router as nc_sync_router
from app.api.v1.nc_coa_sync import router as nc_coa_sync_router
from app.api.v1.qbo import router as qbo_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(budget_router)
api_router.include_router(ap_router)
api_router.include_router(ap_invoices_router)
api_router.include_router(ar_router)
api_router.include_router(payments_router)
api_router.include_router(posting_router)
api_router.include_router(periods_router)
api_router.include_router(coa_router)
api_router.include_router(bank_router)
api_router.include_router(taxreturn_router)
api_router.include_router(gl_router)
api_router.include_router(journal_voucher_router)
api_router.include_router(account_balance_router)
api_router.include_router(admin_router)
api_router.include_router(nc_sync_router)
api_router.include_router(nc_coa_sync_router)
api_router.include_router(qbo_router)
