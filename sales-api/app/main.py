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
    from app.platform.auditing import use_store
    from app.runtime import get_runtime
    from app.services.audit_logs import SqlAuditingStore
    use_store(SqlAuditingStore(AsyncSessionLocal))  # sales36: the audit logs are saved (own session, ≈ ABP requiresNew)
    # A valid token of a user who is no longer active is refused (≈ ABP; user 2026-11-24): the users table, read
    # only, behind a short cache; a refusal is a security event.
    from app.platform.principal import CachedPrincipalStatus, on_inactive, use_principal_status
    from app.services.principals import UsersTablePrincipalStatus
    from app.services.security_log import inactive_listener
    use_principal_status(CachedPrincipalStatus(UsersTablePrincipalStatus(AsyncSessionLocal)))
    on_inactive(inactive_listener(AsyncSessionLocal))
    from app.services.security_log import log_startup
    await log_startup(settings, AsyncSessionLocal)  # the switches (values) and the secrets set (names only)
    runtime = get_runtime()
    from app.services.log_levels import watch
    runtime.background.spawn(watch(AsyncSessionLocal, runtime.background.stop), name="sales-log-levels")
    from app.services.features import refresh as refresh_features, watch as watch_features
    try:
        async with AsyncSessionLocal() as s:
            await refresh_features(s)  # a switch a person turned off stays off from the first request
    except Exception:  # noqa: BLE001 — the tick tries again
        logger.exception("could not read the switches at startup")
    runtime.background.spawn(watch_features(AsyncSessionLocal, runtime.background.stop), name="sales-features")
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
    # Health → Log levels raises one for a while (platform/log_levels.py), "sqlalchemy.engine" for the SQL (no echo, db/session.py).
    # The platform's options, once (the composition root ≈ ABP's Configure<…Options>): ABP's defaults except where a
    # user rule says otherwise (platform/options.py). The audit logs' store is set at startup (lifespan:
    # SqlAuditingStore on sales_audit_logs); before that, and in tests, nothing is saved (NullAuditingStore).
    from app.platform.options import AuditingOptions, LoggingOptions, configure as configure_options
    configure_options(
        AuditingOptions(application_name="sales-api",
                        ignored_routes=("/health", "/health/db", "/api/v1/health", "/api/v1/health/db"),
                        always_log_routes=("*/export.csv", "*.pdf", "*.zip")),  # bulk data handed out: who took it
        LoggingOptions(level=str(settings.LOG_LEVEL).upper(), request_logger="sales.request"))
    from app.platform.log_levels import configure
    configure(str(settings.LOG_LEVEL).upper())
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
        expose_headers=["Content-Disposition"],  # a download's name is the server's
    )

    # The request log (≈ ABP AuditLog, user 2026-11-23): one JSON line per request with its id (platform/request_log.py).
    from app.platform.auditing import AuditingMiddleware
    app.add_middleware(AuditingMiddleware, logger="sales.request")

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
