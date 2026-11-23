"""sales-api FastAPI application factory."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.db.session import engine
from app.platform.errors import install_error_handlers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    logger.info(
        "Starting up %s v%s [%s]",
        settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT,
    )
    from app.db.session import AsyncSessionLocal
    from app.runtime import get_runtime
    from app.services.security_log import log_startup
    await log_startup(settings, AsyncSessionLocal)  # the switches (values) and the secrets set (names only)
    runtime = get_runtime()
    from app.services.log_levels import watch
    runtime.background.spawn(watch(AsyncSessionLocal, runtime.background.stop), name="sales-log-levels")
    if settings.SALES_SCHEDULER_ENABLED:
        runtime.background.spawn(runtime.scheduler.loop(runtime.background.stop), name="sales-scheduler")
        logger.info("scheduler started")
    else:
        logger.info("scheduler disabled (SALES_SCHEDULER_ENABLED=false); Run now still works")
    yield
    logger.info("Shutting down — draining background tasks, then closing the engine")
    await runtime.background.drain(timeout=10)
    await engine.dispose()


def create_app() -> FastAPI:
    # One logging setup (user 2026-11-23): JSON lines on stdout at LOG_LEVEL; loud third-party loggers quiet;
    # Health → Log levels raises one for a while (platform/log_levels.py). DEBUG's SQL echo keeps its own output.
    from app.platform.log_levels import configure
    configure(str(settings.LOG_LEVEL).upper())
    if settings.DEBUG:
        logging.getLogger("sqlalchemy.engine.Engine").propagate = False  # echo prints it already: no second copy
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
    )

    # The request log (≈ ABP AuditLog, user 2026-11-23): one JSON line per request with its id (platform/request_log.py).
    from app.platform.request_log import RequestLogMiddleware
    app.add_middleware(RequestLogMiddleware, logger="sales.request")

    # BusinessError → {"error": {code, message, details}} with 409/422;
    # anything else → 500 with a correlation id (platform README).
    install_error_handlers(app)

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # Also expose health checks at root so docker-compose healthchecks
    # and check-health.sh (`/health`) work without the /api/v1 prefix.
    from app.api.v1.health import router as health_router
    app.include_router(health_router)

    return app


app = create_app()
