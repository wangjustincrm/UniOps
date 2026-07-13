"""Test fixtures for finance-api.

Schema is built by running the real alembic migrations against a dedicated
local Postgres database (default: finance_test on the uniops_postgres docker
container). Mirrors budget-api/tests/conftest.py.

NOTE: migration 0001 has a FK to payment_applications (an epms-api table),
so we pre-create a one-column stub before running alembic.
"""
import os
import subprocess
import sys

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_DB = os.getenv("TEST_FINANCE_DB", "finance_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
ADMIN_SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/postgres"


def _ensure_db():
    eng = sa.create_engine(ADMIN_SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB}"'))
    eng.dispose()


def _migrate():
    """Reset public schema, create mirror tables, run alembic upgrade head.

    Mirror tables (payment_applications / invoices / company_config /
    expense_claims) are owned by other services in the real DB; tests build
    them from this service's mirror models so the payment executor has real
    columns to work with. 0001's FK target (payment_applications) is among them.
    """
    _ensure_db()
    eng = sa.create_engine(SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    eng.dispose()

    from app.db.base import Base
    from app.models.admin_audit_log import AdminAuditLog  # shared table, no finance migration owns it
    from app.models.mirrors import (  # noqa: F401
        CompanyConfig, CostCenter, Department, ExpenseApprovalEvent, ExpenseClaim,
        ExpenseLineItem, ExpenseTripItem, Invoice, InvoiceTaxLine, SodRule, Task, User,
    )
    from app.models.pa import PaymentApplication
    eng = sa.create_engine(SYNC_URL)
    Base.metadata.create_all(eng, tables=[
        PaymentApplication.__table__, Invoice.__table__, InvoiceTaxLine.__table__,
        CompanyConfig.__table__, ExpenseClaim.__table__, Task.__table__,
        SodRule.__table__, User.__table__, ExpenseApprovalEvent.__table__,
        ExpenseLineItem.__table__, ExpenseTripItem.__table__,
        AdminAuditLog.__table__, CostCenter.__table__,
        Department.__table__,
    ])
    eng.dispose()
    # PaymentApplication mirror needs pa_type for A4 partial-payment logic

    env = dict(os.environ)
    env["DATABASE_URL"] = ASYNC_URL
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
async def db_session():
    _migrate()
    engine = create_async_engine(ASYNC_URL, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
