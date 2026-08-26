"""Application factory and startup/shutdown lifecycle."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.task_logging import configure_task_logging
from app.db.redis import close_redis
from app.db.session import engine

logger = logging.getLogger(__name__)

# Scheduler INFO logs (app.tasks.*) are otherwise silently dropped — see
# app/core/task_logging.py for why this doesn't just flip root to INFO.
configure_task_logging()

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up %s v%s [%s]", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)
    from app.tasks.daily_followup import daily_followup_loop
    followup_task = asyncio.create_task(daily_followup_loop())
    from app.tasks.agreement_overdue import agreement_overdue_loop
    overdue_task = asyncio.create_task(agreement_overdue_loop())
    # Keeps the NC65 purchase mirror current on a schedule instead of on
    # somebody remembering to press the button in Admin.
    from app.tasks.nc_purchase_sync_scheduler import nc_purchase_sync_loop
    nc_sync_task = asyncio.create_task(nc_purchase_sync_loop())
    # Nudges the PR requester to create a GR once a service/project PO passes
    # its expected completion date (PRD GR-S-001a). Gated OFF by default.
    from app.tasks.service_gr_due import service_gr_due_loop
    service_gr_task = asyncio.create_task(service_gr_due_loop())
    yield
    logger.info("Shutting down — closing connections")
    followup_task.cancel()
    overdue_task.cancel()
    nc_sync_task.cancel()
    service_gr_task.cancel()
    # Settle fire-and-forget work (notification emails, PDF generation, PMS
    # import) BEFORE disposing the engine. Each of those owns its own session;
    # tearing the pool down underneath one leaves it mid-transaction. Brief
    # grace period, then cancel so shutdown can't hang on a slow SMTP server.
    from app.core.background import drain
    drained = await drain(timeout=10.0)
    if drained:
        logger.info("Settled %d background task(s) before shutdown", drained)
    await engine.dispose()
    await close_redis()


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
    )

    # ── Request logging middleware (pure ASGI — no BaseHTTPMiddleware task spawn) ─
    from starlette.types import ASGIApp, Receive, Scope, Send

    class _LoggingMiddleware:
        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            request = Request(scope)
            start = time.perf_counter()
            status_code = 500

            async def _send_wrapper(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                await send(message)

            try:
                await self.app(scope, receive, _send_wrapper)
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                logger.info(
                    "%s %s %d %.1fms",
                    request.method,
                    request.url.path,
                    status_code,
                    duration_ms,
                )

    app.add_middleware(_LoggingMiddleware)

    # ── Global exception handler ──────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    return app


app = create_app()
