"""Expense API — entry point."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1.health import router as health_router
from app.api.v1.pa import router as pa_router
from app.api.v1.stats import router as stats_router
from app.api.v1.expenses import router as expenses_router
from app.api.v1.policy import router as policy_router
# NOTE: budget router moved to budget-api (:8007). OA frontend calls budget-api directly
# for /accounts and /hierarchy.
from app.api.v1.invoices import router as invoices_router
from app.api.v1.invoice_attachments import router as invoice_attachments_router
from app.api.v1.expense_attachments import router as expense_attachments_router
from app.api.v1.invoice_list import router as invoice_list_router
from app.api.v1.vendors import router as vendors_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.ocr import router as ocr_router
from app.api.v1.custom_forms import router as custom_forms_router
from app.api.v1.admin import router as admin_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
    )

    app.include_router(health_router)
    app.include_router(pa_router, prefix="/api/v1")
    # custom_forms_router MUST be registered before expenses_router:
    # GET /expenses/custom-forms must match before /expenses/{claim_id} (uuid param).
    app.include_router(custom_forms_router, prefix="/api/v1")
    app.include_router(expenses_router, prefix="/api/v1")
    app.include_router(policy_router, prefix="/api/v1")
    # invoice_list_router MUST be registered before invoices_router:
    # GET /invoices/all must match the literal route before /{invoice_id} (uuid param).
    app.include_router(invoice_list_router, prefix="/api/v1")
    app.include_router(invoices_router, prefix="/api/v1")
    app.include_router(invoice_attachments_router, prefix="/api/v1")
    app.include_router(expense_attachments_router, prefix="/api/v1")
    app.include_router(vendors_router, prefix="/api/v1")
    app.include_router(tasks_router, prefix="/api/v1")
    app.include_router(ocr_router, prefix="/api/v1")
    app.include_router(stats_router)
    app.include_router(admin_router, prefix="/api/v1")

    @app.on_event("startup")
    async def _startup():
        log.info("expense-api starting on port %d", settings.port)
        # Check for pending Alembic migrations
        try:
            from alembic.config import Config
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory
            from app.db.base import engine
            alembic_cfg = Config("alembic.ini")
            script = ScriptDirectory.from_config(alembic_cfg)
            async with engine.connect() as conn:
                context = MigrationContext.configure(await conn.get_raw_connection())
                current = set(context.get_current_heads())
                heads = set(script.get_heads())
                pending = heads - current
                if pending:
                    log.warning(
                        "expense-api has %d pending migration(s). Run: alembic upgrade head",
                        len(pending),
                    )
        except Exception:
            pass  # Migration check is best-effort

    return app


app = create_app()
