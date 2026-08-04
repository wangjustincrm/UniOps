"""Test fixtures for mrp-api.

The test schema is built by running the real alembic migrations against a
dedicated Postgres database (default: local `mrp_test`). This is faithful to
production and also exercises the migration chain. We avoid
metadata.create_all() in favor of the same pattern used by budget-api /
mdm-api's conftest.py.
"""
import os
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from jose import jwt as _jwt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.session import get_session
from app.main import app

# Local dev Postgres test database. Override host/db via env if needed.
TEST_DB = os.getenv("TEST_MRP_DB", "mrp_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"


def _migrate():
    """Reset the public schema and run alembic upgrade head against the test DB."""
    eng = sa.create_engine(SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    eng.dispose()

    env = dict(os.environ)
    env.update({
        "POSTGRES_HOST": TEST_HOST, "POSTGRES_PORT": TEST_PORT,
        "POSTGRES_USER": TEST_USER, "POSTGRES_PASSWORD": TEST_PASSWORD,
        "POSTGRES_DB": TEST_DB,
    })
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root, env=env, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_engine():
    _migrate()
    engine = create_async_engine(ASYNC_URL, echo=False, connect_args={"ssl": False})
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        yield session


# ── HTTP endpoint test harness ──────────────────────────────────────────────────
#
# SAFETY: the `client` fixture MUST override `get_session` to the test
# `db_session` — otherwise the app would connect via `settings.DATABASE_URL`,
# which in this worktree could point at the shared production DB. Never let
# an endpoint test hit the real engine.

@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _token(sub: uuid.UUID, role: str) -> str:
    return _jwt.encode(
        {"sub": str(sub), "role": role, "type": "access"},
        settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )


@pytest_asyncio.fixture
async def admin_token():
    return _token(uuid.uuid4(), "system_admin")
