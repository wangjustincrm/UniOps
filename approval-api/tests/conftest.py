"""Minimal test fixtures for approval-api (first tests in this service).

approval-api owns no schema (it mirrors other services' tables), so we
create ONLY the posting tables it writes to, on a dedicated local DB.
Engine state-machine tests (P1) will extend this conftest.
"""
import os

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.posting import PostingEvent, PostingLine
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User

TEST_DB = os.getenv("TEST_APPROVAL_DB", "approval_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
ADMIN_SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/postgres"

_POSTING_TABLES = [PostingEvent.__table__, PostingLine.__table__]


def _build_schema():
    admin = sa.create_engine(ADMIN_SYNC_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()

    eng = sa.create_engine(SYNC_URL)
    from app.db.base import Base
    Base.metadata.drop_all(eng, tables=_POSTING_TABLES)
    Base.metadata.create_all(eng, tables=_POSTING_TABLES)
    eng.dispose()


@pytest_asyncio.fixture
async def db_session():
    _build_schema()
    engine = create_async_engine(ASYNC_URL, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


# ── Engine state-machine fixtures ────────────────────────────────────────────
# Tables the engine touches for document workflow execution (PA-DIR path).

_ENGINE_TABLES = [
    User.__table__,
    CompanyConfig.__table__,
    PaymentApplication.__table__,
    PurchaseRequest.__table__,
    Task.__table__,
    ApprovalEvent.__table__,
]


def _build_engine_schema():
    admin = sa.create_engine(ADMIN_SYNC_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()

    eng = sa.create_engine(SYNC_URL)
    from app.db.base import Base
    Base.metadata.drop_all(eng, tables=_ENGINE_TABLES)
    Base.metadata.create_all(eng, tables=_ENGINE_TABLES)
    eng.dispose()


@pytest_asyncio.fixture
async def engine_db_session():
    _build_engine_schema()
    engine = create_async_engine(ASYNC_URL, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
