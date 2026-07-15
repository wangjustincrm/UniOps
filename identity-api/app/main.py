"""Identity API — authentication, SoD rules, universal audit log (Phase 0-B4).

Owns: auth (moved from epms-api; epms /auth/* is now a thin proxy here),
sod_rules, audit_log. Shares the users / company_config tables (schema still
owned by epms alembic — code ownership moved first, table custody later).
Tokens are identical to epms-issued ones: same secret, same claims — no other
service changes.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.auth import router as auth_router
from app.api.v1.authz import router as authz_router
from app.api.v1.health import router as health_router
from app.api.v1.sod import router as sod_router
from app.core.config import settings
from app.db.redis import close_redis
from app.models import audit, config, sod, user  # noqa: F401 — register metadata

app = FastAPI(title="UniOps Identity API", version="1.0.0")

app.add_middleware(
    CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)

app.include_router(auth_router, prefix="/identity/v1")
app.include_router(authz_router, prefix="/identity/v1")
app.include_router(sod_router, prefix="/identity/v1")
app.include_router(health_router)


@app.on_event("shutdown")
async def _shutdown():
    await close_redis()
