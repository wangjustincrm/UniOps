"""authz package test fixtures — authz_test DB with raw-SQL shadow tables.

Mirrors identity-api/tests/conftest.py's build-a-test-DB pattern (session
engine, session-scoped event loop forced onto every async test item), but
this package has no ORM models of its own by design, so the shadow tables
are minimal raw SQL rather than Base.metadata.create_all. Column definitions
were checked against the live tables via
`docker exec uniops_postgres psql -U epms -d epms -c "\\d user_roles"` etc.
(see the task-1 brief) — only the columns the gate actually reads/writes are
reproduced, and no FK constraints: the pinned tests insert role/permission
codes ad hoc and exercise is_active semantics, not referential integrity.
"""
import asyncio
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB = os.getenv("TEST_AUTHZ_DB", "authz_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
ADMIN_SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/postgres"
SCHEMA_SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"

DDL_STATEMENTS = [
    "DROP TABLE IF EXISTS role_permission_locks CASCADE",
    "DROP TABLE IF EXISTS role_permissions CASCADE",
    "DROP TABLE IF EXISTS user_roles CASCADE",
    "DROP TABLE IF EXISTS permission_defs CASCADE",
    "DROP TABLE IF EXISTS role_defs CASCADE",
    "DROP TABLE IF EXISTS users CASCADE",
    """
    CREATE TABLE users (
        id uuid PRIMARY KEY,
        email varchar(255) NOT NULL,
        hashed_password varchar(255) NOT NULL,
        full_name varchar(255) NOT NULL,
        role varchar(50) NOT NULL DEFAULT 'requester',
        is_active boolean NOT NULL DEFAULT true
    )
    """,
    """
    CREATE TABLE role_defs (
        code varchar(50) PRIMARY KEY,
        label varchar(100) NOT NULL,
        sort integer NOT NULL DEFAULT 0,
        is_active boolean NOT NULL DEFAULT true
    )
    """,
    """
    CREATE TABLE permission_defs (
        key varchar(64) PRIMARY KEY,
        module varchar(20) NOT NULL,
        label varchar(120) NOT NULL,
        sort integer NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE user_roles (
        user_id uuid NOT NULL,
        role_code varchar(50) NOT NULL,
        PRIMARY KEY (user_id, role_code)
    )
    """,
    """
    CREATE TABLE role_permissions (
        role_code varchar(50) NOT NULL,
        permission_key varchar(64) NOT NULL,
        updated_by uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (role_code, permission_key)
    )
    """,
    """
    CREATE TABLE role_permission_locks (
        role_code varchar(50) NOT NULL,
        permission_key varchar(64) NOT NULL,
        PRIMARY KEY (role_code, permission_key)
    )
    """,
]

# Roles the pinned tests use as ADDITIONAL roles (via user_roles) without
# ever inserting a role_defs row themselves. user_role_codes() inner-joins on
# role_defs.is_active, so a bare role code with no role_defs row would be
# silently dropped — this baseline keeps those tests about permission
# semantics, not role_defs bookkeeping.
# test_inactive_additional_role_ignored inserts its own role_defs row
# (is_active=false) for 'retired_role' and is unaffected by this baseline.
BASELINE_ROLES = ["finance_bp", "ap_clerk"]

# Permission keys the pinned tests grant/lock via role_permissions /
# role_permission_locks without ever inserting a permission_defs row
# themselves. effective_permissions() only returns keys sourced from
# permission_defs (that's what lets an ungranted key read False instead of
# being absent) — a key missing from permission_defs never appears in its
# result at all, so tests asserting `.get(key) is True` need the key
# pre-registered. test_ungranted_key_is_false_not_missing and
# test_inactive_additional_role_ignored insert their own permission_defs rows
# (or don't need one, since they only assert `is not True`) and are
# unaffected by this baseline.
BASELINE_PERMISSIONS = ["k.write", "k.locked"]


def _ensure_db():
    eng = sa.create_engine(ADMIN_SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB}"'))
    eng.dispose()


def _reset_schema():
    eng = sa.create_engine(SCHEMA_SYNC_URL)
    with eng.begin() as conn:
        for stmt in DDL_STATEMENTS:
            conn.execute(sa.text(stmt))
    eng.dispose()


def pytest_collection_modifyitems(items):
    session_mark = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if isinstance(item, pytest.Function) and asyncio.iscoroutinefunction(item.function):
            item.add_marker(session_mark, append=False)


@pytest.fixture(scope="session")
def test_engine():
    _ensure_db()
    _reset_schema()
    engine = create_async_engine(ASYNC_URL, echo=False)
    yield engine


@pytest.fixture
async def authz_db(test_engine):
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        for tbl in ("role_permission_locks", "role_permissions", "user_roles",
                    "permission_defs", "role_defs", "users"):
            await session.execute(sa.text(f"DELETE FROM {tbl}"))
        for code in BASELINE_ROLES:
            await session.execute(sa.text(
                "INSERT INTO role_defs (code, label, sort, is_active) "
                "VALUES (:c, :c, 0, true)"), {"c": code})
        for key in BASELINE_PERMISSIONS:
            await session.execute(sa.text(
                "INSERT INTO permission_defs (key, module, label, sort) "
                "VALUES (:k, 'test', :k, 0)"), {"k": key})
        await session.commit()
        yield session
