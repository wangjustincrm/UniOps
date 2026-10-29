"""Test fixtures for orders-api.

Tests run only in a throwaway container against a database whose name ends in
`_test` (default `orders_test`). The schema is built by running the real
alembic migrations, as inventory-api does. Never run these with the host
`.env`: it points at production.
"""
import os
import subprocess
import sys
import uuid

# Before any app import: the settings object is built at import time.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ["ENVIRONMENT"] = "test"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from jose import jwt as _jwt  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import app  # noqa: E402

TEST_DB = os.getenv("TEST_ORDERS_DB", "orders_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"


def _refuse_non_test_db() -> None:
    # DROP SCHEMA CASCADE below must never reach a real database: a host .env
    # in this repo has pointed POSTGRES_* at production before.
    if not TEST_DB.endswith("_test"):
        raise RuntimeError(
            f"refusing to use database {TEST_DB!r} — TEST_ORDERS_DB must end in '_test'"
        )


def _migrate() -> None:
    """Reset the public schema and run alembic upgrade head against the test DB."""
    _refuse_non_test_db()
    eng = sa.create_engine(SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    eng.dispose()

    run_alembic("upgrade", "head", check=True)


def run_alembic(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """alembic against the test database (never the host .env)."""
    _refuse_non_test_db()
    env = dict(os.environ)
    env.update({
        "POSTGRES_HOST": TEST_HOST, "POSTGRES_PORT": TEST_PORT,
        "POSTGRES_USER": TEST_USER, "POSTGRES_PASSWORD": TEST_PASSWORD,
        "POSTGRES_DB": TEST_DB,
    })
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=root, env=env, check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


@pytest.fixture(scope="session")
def migrated_db():
    _migrate()
    return ASYNC_URL


@pytest_asyncio.fixture
async def db_engine(migrated_db):
    engine = create_async_engine(migrated_db, echo=False, connect_args={"ssl": False})
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        yield session


# SAFETY: the `client` fixture overrides `get_session` with the test session —
# an endpoint test must never reach the engine built from settings.
@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def make_token(role: str, sub: uuid.UUID | None = None) -> str:
    return _jwt.encode(
        {"sub": str(sub or uuid.uuid4()), "role": role, "type": "access"},
        settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {make_token('system_admin')}"}
