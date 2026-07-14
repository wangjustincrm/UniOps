from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import api_router
from app.api.v1.health import router as health_router
from app.core.config import settings
# NOTE: budget model removed — budget data lives in budget-api (:8007).
# finance-api's budget endpoints now proxy to budget-api via HTTP.
from app.models import admin_audit_log, ap_invoice, bank, coa, fiscal_period, mirrors, nc_customer, nc_sync, pa, payment, payment_batch, posting  # noqa: F401 — register with metadata

app = FastAPI(
    title="UniOps Finance Core P1",
    description="Budget management · Accounts Payable · Payment recording — Phase 1",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)

app.include_router(api_router, prefix="/finance/v1")
app.include_router(health_router)
