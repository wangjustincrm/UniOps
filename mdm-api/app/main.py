from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import api_router
from app.db.base import engine, Base
from app.core.config import settings
# Import models so Alembic/metadata can see them
from app.models import vendor, department, cost_center, part, user, company, erp_material, erp_supplier, erp_person, erp_sync_state, uom, material, uom_conversion, nc_bom, bom, material_supplier, sync_state, company_config  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    from app.tasks.erp_sync_scheduler import erp_sync_loop
    scheduler_task = asyncio.create_task(erp_sync_loop())
    yield
    scheduler_task.cancel()


app = FastAPI(
    title="UniOps MDM Stub",
    description="Master Data Management — Phase 1 read-only proxy + Company master",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)

app.include_router(api_router, prefix="/mdm/v1")
# Health endpoints at root level too
from app.api.v1.health import router as health_router  # noqa: E402
app.include_router(health_router)
