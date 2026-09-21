import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import api_router
from app.api.v1.health import router as health_router
from app.core.config import settings
from app.core.task_logging import configure_task_logging
# NOTE: budget model removed — budget data lives in budget-api (:8007).
# finance-api's budget endpoints now proxy to budget-api via HTTP.
from app.models import admin_audit_log, ap_invoice, bank, coa, fiscal_period, mirrors, nc_ap, nc_customer, nc_export, nc_sync, pa, payment, payment_batch, posting  # noqa: F401 — register with metadata

# Scheduler INFO logs (app.tasks.*) are otherwise silently dropped — see
# app/core/task_logging.py for why this doesn't just flip root to INFO.
configure_task_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.tasks.nc_ap_sync_scheduler import nc_ap_sync_loop
    from app.tasks.nc_sync_scheduler import nc_sync_loop
    # Both loops are held in locals for the lifetime of the app: a Task that
    # nobody references can be garbage-collected mid-await.
    scheduler_task = asyncio.create_task(nc_sync_loop())
    ap_scheduler_task = asyncio.create_task(nc_ap_sync_loop())
    yield
    scheduler_task.cancel()
    ap_scheduler_task.cancel()


app = FastAPI(
    title="UniOps Finance Core P1",
    description="Budget management · Accounts Payable · Payment recording — Phase 1",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
    # Content-Disposition must be exposed or cross-origin fetch() can't read the
    # NC export filename (AP number) and falls back to a generic name.
    expose_headers=["Content-Disposition"],
)

app.include_router(api_router, prefix="/finance/v1")
app.include_router(health_router)
