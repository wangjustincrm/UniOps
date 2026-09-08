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
from app.models.delegation import ApprovalDelegation
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.posting import PostingEvent, PostingLine
from app.models.pr import PurchaseRequest
from app.models.routing import ApprovalBackup, ApprovalSetting, DeptRouting
from app.models.agreement import PurchaseAgreement
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
    PurchaseOrder.__table__,
    PurchaseRequest.__table__,
    # Agreements route through the same engine (agr action key); routing tests
    # insert real rows, so the table must exist in the engine schema.
    PurchaseAgreement.__table__,
    Task.__table__,
    ApprovalEvent.__table__,
    DeptRouting.__table__,
    ApprovalBackup.__table__,
    ApprovalSetting.__table__,
    ApprovalDelegation.__table__,
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
    # The delegation table's EXCLUDE constraint needs btree_gist. Trusted in
    # PG 13+, and this fixture owns the database it just created.
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.create_all(eng, tables=_ENGINE_TABLES)
    # `user_roles` is identity-owned (no ORM model here — see
    # tests/test_seed_routing.py's shadow-table idiom); the engine now reads it
    # unconditionally on every execute_action call (app.crud.workflow.
    # get_role_management, Task 4), so every engine test needs the table to at
    # least exist. Rebuilt empty on each call; tests that care about its rows
    # (test_routing_adapters.py, test_seed_routing.py) drop/recreate it locally
    # with the columns they need.
    with eng.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS user_roles CASCADE"))
        conn.execute(sa.text(
            "CREATE TABLE user_roles (user_id uuid NOT NULL, role_code varchar(50) NOT NULL,"
            " PRIMARY KEY (user_id, role_code))"))
        # `pa_po_links` is epms-owned (same shadow-table idiom as user_roles
        # above). The engine reads it on EVERY pa action — a payment covering
        # purchase orders from several departments skips its department-scoped
        # steps — so it has to exist even for tests that never populate it.
        conn.execute(sa.text("DROP TABLE IF EXISTS pa_po_links CASCADE"))
        conn.execute(sa.text(
            "CREATE TABLE pa_po_links (id uuid PRIMARY KEY, pa_id uuid NOT NULL,"
            " po_id uuid NOT NULL, po_number varchar(40) NOT NULL,"
            " sort_order integer NOT NULL DEFAULT 0)"))
    eng.dispose()


@pytest_asyncio.fixture
async def engine_db_session():
    _build_engine_schema()
    engine = create_async_engine(ASYNC_URL, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
