"""Test fixtures for budget-api.

The test schema is built by running the real alembic migrations against a
dedicated Postgres database (default: local `budget_test`). This is faithful to
production and also exercises the migration chain — notably the 'opening' ledger
operation constraint. We avoid metadata.create_all() because some partial-index
definitions use deferred lambdas that only the migrations render correctly.
"""
import os
import subprocess
import sys

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Local dev Postgres test database. Override host/db via env if needed.
TEST_DB = os.getenv("TEST_BUDGET_DB", "budget_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"


def _create_scope_stub_tables(engine):
    """Minimal stand-ins for identity/epms-owned tables budget_scope.py reads.

    `users`, `user_roles`, and `cost_centers` live in another service's schema
    in production (same physical DB); budget-api's own alembic chain doesn't
    own or migrate them. Test scaffolding only — mirrors the pattern in
    finance-api/tests/conftest.py (shadow user_roles / mirror User & CostCenter).
    Only the columns app/core/budget_scope.py actually reads are included.
    """
    with engine.connect() as conn:
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS users ("
            " id uuid PRIMARY KEY,"
            " department_id uuid,"
            " role text,"
            " is_active boolean NOT NULL DEFAULT true"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS user_roles ("
            " user_id uuid NOT NULL,"
            " role_code text NOT NULL"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS cost_centers ("
            " id uuid PRIMARY KEY,"
            " code text,"
            " name text,"
            " department_id uuid,"
            " is_active boolean NOT NULL DEFAULT true"
            ")"
        ))
        conn.commit()


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

    eng = sa.create_engine(SYNC_URL, isolation_level="AUTOCOMMIT")
    _create_scope_stub_tables(eng)
    eng.dispose()


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
