from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.v1 import api_router
from app.api.v1.health import router as health_router
from app.core.config import settings
from app.crud.engine import _WORKFLOW_DEFAULTS
from app.db.base import AsyncSessionLocal
from app.models.config import CompanyConfig


async def seed_default_workflows() -> None:
    """Populate CompanyConfig.workflow_defs with PRD defaults for any missing action keys.

    Only fills gaps — never overwrites keys the admin has already configured.
    Skips silently if CompanyConfig doesn't exist yet (epms-api initialises it).
    """
    async with AsyncSessionLocal() as db:
        cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
        if cfg is None:
            return  # epms-api hasn't seeded CompanyConfig yet; skip

        current: dict = dict(cfg.workflow_defs or {})
        added = [k for k, v in _WORKFLOW_DEFAULTS.items() if k not in current]
        if not added:
            return

        for key in added:
            current[key] = _WORKFLOW_DEFAULTS[key]

        cfg.workflow_defs = current
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed_default_workflows()
    yield


app = FastAPI(
    title="UniOps Approval Engine",
    description="Configurable workflow execution engine for all UniOps modules (PR/PO/PA/PA-DIR/EXP/MIL/TRV/CFM)",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)

app.include_router(api_router, prefix="/approval/v1")
app.include_router(health_router)
