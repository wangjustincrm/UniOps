"""Shared pytest fixtures for expense-api test suite.
Mirrors epms-api/tests/conftest.py — test DB is `expense_test`.
Requires: python -m scripts.create_test_db (run once).
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import ALL models so Base.metadata.create_all() registers every table
import app.models.expense           # noqa: F401
import app.models.invoice           # noqa: F401
import app.models.invoice_attachment  # noqa: F401
import app.models.pa                # noqa: F401
import app.models.policy            # noqa: F401
import app.models.epms_mirrors      # noqa: F401
import app.models.approval_event_mirror  # noqa: F401
import app.models.company_config_mirror  # noqa: F401
import app.models.task_mirror           # noqa: F401
import app.models.admin_audit_log       # noqa: F401
import app.models.posting_mirror        # noqa: F401
import app.db.base as db_module
from app.core.config import settings
from app.db.base import Base
from app.main import create_app

# Derive test DB URL from settings (swap DB name)
_base, _ = settings.database_url.rsplit("/", 1)
_TEST_DB_URL = f"{_base}/expense_test"

_JWT_SECRET = settings.jwt_secret_key
_JWT_ALG = settings.jwt_algorithm


# ── Loop scope ────────────────────────────────────────────────────────────────

def pytest_collection_modifyitems(items):
    session_mark = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if isinstance(item, pytest.Function) and asyncio.iscoroutinefunction(item.function):
            item.add_marker(session_mark, append=False)


# ── Test engine ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # `user_roles` is identity-owned (no ORM model here — pa.py's
        # _user_role_codes reads it directly, same physical DB in prod,
        # phase-3 Task 5). Shadow it so tests can grant additional roles.
        from sqlalchemy import text
        await conn.execute(text("DROP TABLE IF EXISTS user_roles CASCADE"))
        await conn.execute(text(
            "CREATE TABLE user_roles (user_id uuid NOT NULL, role_code varchar(50) NOT NULL,"
            " PRIMARY KEY (user_id, role_code))"))
        # Same story for the Access Control matrix (identity-owned, same
        # physical DB in prod, read with raw SQL by app/core/authz_matrix.py).
        # These must exist for EVERY test, not just the ones that seed them:
        # invoice_attachments' authz reads them on every call, so without the
        # shadow any unrelated attachment test blows up on UndefinedTable.
        for ddl in (
            "CREATE TABLE role_defs (code varchar(50) PRIMARY KEY,"
            " is_active boolean NOT NULL DEFAULT true)",
            "CREATE TABLE role_permissions (role_code varchar(50) NOT NULL,"
            " permission_key varchar(100) NOT NULL, PRIMARY KEY (role_code, permission_key))",
            "CREATE TABLE role_permission_locks (role_code varchar(50) NOT NULL,"
            " permission_key varchar(100) NOT NULL, PRIMARY KEY (role_code, permission_key))",
        ):
            table = ddl.split()[2]
            await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
            await conn.execute(text(ddl))
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(test_engine):
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest.fixture(scope="session", autouse=True)
async def _patch_session_factory(test_engine):
    original = db_module.AsyncSessionLocal
    db_module.AsyncSessionLocal = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False,
    )
    yield
    db_module.AsyncSessionLocal = original


@pytest.fixture(scope="session", autouse=True)
def _isolate_finance_sync():
    """Never let the test suite reach a live finance-api.

    Unmocked tests (invoice/PA create) fire a real fail-open `upsert_ap_invoice`
    HTTP call to `settings.finance_api_url`. If that env points at a reachable
    finance-api (e.g. running pytest inside a dev container, which sets
    FINANCE_API_URL=http://finance-api:8004 and DATABASE_URL at production),
    every created invoice leaks a real row into the production finance
    `ap_invoices` table. This happened 2026-06-24 (65 orphan `Titan Power Ltd`
    AP invoices in prod). Blanking the URL disables AP sync in `upsert_ap_invoice`
    (returns immediately, no socket) regardless of env. Tests that need the real
    HTTP path (e.g. test_upsert_ap_invoice_fail_open) re-set a non-blank URL."""
    original = settings.finance_api_url
    settings.finance_api_url = ""
    yield
    settings.finance_api_url = original


# ── Token helper ──────────────────────────────────────────────────────────────

def _make_token(role: str, user_id: str | None = None) -> str:
    return jwt.encode(
        {
            "sub": user_id or str(uuid.uuid4()),
            "role": role,
            "exp": datetime.utcnow() + timedelta(hours=8),
        },
        _JWT_SECRET,
        algorithm=_JWT_ALG,
    )


# ── HTTP clients ──────────────────────────────────────────────────────────────

def _client(token: str) -> AsyncClient:
    app = create_app()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
async def client():
    """Unauthenticated client."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_client():
    async with _client(_make_token("system_admin")) as c:
        yield c


@pytest.fixture
async def finance_client():
    async with _client(_make_token("finance_manager")) as c:
        yield c


@pytest.fixture
async def finance_bp_client():
    async with _client(_make_token("finance_bp")) as c:
        yield c


@pytest.fixture
async def requester_client():
    async with _client(_make_token("requester")) as c:
        yield c


@pytest.fixture
async def requester_client_b():
    """A second requester — different user_id."""
    async with _client(_make_token("requester")) as c:
        yield c
