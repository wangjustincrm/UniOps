"""vms-api FastAPI application factory.

Mirrors the budget-api / expense-api shape so tooling (check-health.sh,
docker-compose healthcheck, Portal task inbox) treats VMS like any other
UniOps microservice.
"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.db.session import engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    logger.info(
        "Starting up %s v%s [%s]",
        settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT,
    )
    # Background scheduler — reminders / no-show / overdue alerts
    # (PRD VMS-PR-012/-019, VMS-CO-010/-011). No-op when SCHEDULER_ENABLED=false.
    from app.services import scheduler
    scheduler_task = scheduler.start(app)
    yield
    logger.info("Shutting down — stopping scheduler + closing engine")
    await scheduler.stop(scheduler_task)
    await engine.dispose()


def create_app() -> FastAPI:
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

    # Pure ASGI request logger (avoids BaseHTTPMiddleware task spawn).
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
                    request.method, request.url.path, status_code, duration_ms,
                )

    app.add_middleware(_LoggingMiddleware)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):  # noqa: ARG001
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # Also expose /health at root so docker-compose healthchecks + check-health.sh
    # work without the /api/v1 prefix.
    from app.api.v1.health import router as health_router
    app.include_router(health_router)

    return app


app = create_app()
