"""Shared fixtures for the ehs-api suite.

Mirrors booking-api's conftest. The test database is `ehs_test` unless
TEST_EHS_DB says otherwise — the knob is that variable, not POSTGRES_DB, which
would also redirect the app's own configuration.

ehs-api does not own `users`, `tasks`, `departments` or `locations`, but its
foreign keys point at them, so `Base.metadata.create_all` materializes the
mirrors here. It also shadows identity's authz tables: uniops_authz reads them
with raw SQL against the same physical database in production, so without them
every permission check would fail for a reason unrelated to what is under test.
"""
import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.session as session_module
import app.models  # noqa: F401 — registers every model with Base.metadata
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models.mirrors import User

_base_url, _ = str(settings.DATABASE_URL).rsplit("/", 1)
_TEST_DB_URL = f"{_base_url}/{os.getenv('TEST_EHS_DB', 'ehs_test')}"

_JWT_SECRET = settings.JWT_SECRET_KEY
_JWT_ALG = settings.JWT_ALGORITHM

# Roles that exist before Safety is installed, plus the six it adds.
BASE_ROLES = ("requester", "supervisor", "gm", "opm", "auditor", "system_admin")
EHS_ROLES = ("ehs_manager", "ehs_coordinator", "area_supervisor",
             "jhsc_member", "first_aider", "worker")
ALL_ROLES = BASE_ROLES + EHS_ROLES

# Mirrors identity-api/alembic/versions/0013_ehs_perms.py.
EHS_KEYS = (
    "ehs.incident.report", "ehs.incident.read", "ehs.incident.investigate",
    "ehs.incident.close", "ehs.incident.medical.read",
    "ehs.action.read", "ehs.action.write", "ehs.action.verify",
    "ehs.firstaid.write", "ehs.training.read", "ehs.training.write",
    "ehs.worker.read", "ehs.worker.write",
    "ehs.statutory.manage", "ehs.settings.manage",
)
EHS_GRANTS = {
    "ehs.incident.read": ["ehs_manager", "ehs_coordinator", "area_supervisor",
                          "jhsc_member", "auditor"],
    "ehs.incident.investigate": ["ehs_manager", "ehs_coordinator", "area_supervisor"],
    "ehs.incident.close": ["ehs_manager"],
    "ehs.incident.medical.read": ["ehs_manager", "first_aider"],
    "ehs.action.read": ["ehs_manager", "ehs_coordinator", "area_supervisor",
                        "jhsc_member", "worker", "auditor"],
    "ehs.action.write": ["ehs_manager", "ehs_coordinator", "area_supervisor"],
    "ehs.action.verify": ["ehs_manager", "ehs_coordinator"],
    "ehs.firstaid.write": ["ehs_manager", "ehs_coordinator", "first_aider"],
    "ehs.training.read": ["ehs_manager", "ehs_coordinator", "area_supervisor", "auditor"],
    "ehs.training.write": ["ehs_manager", "ehs_coordinator"],
    "ehs.worker.read": ["ehs_manager", "ehs_coordinator", "area_supervisor"],
    "ehs.worker.write": ["ehs_manager", "ehs_coordinator"],
    "ehs.statutory.manage": ["ehs_manager"],
    "ehs.settings.manage": ["ehs_manager"],
}

# Ontario statutory holidays for the years the tests reach into.
TEST_HOLIDAYS = [
    (2026, date(2026, 1, 1), "New Year's Day"),
    (2026, date(2026, 2, 16), "Family Day"),
    (2026, date(2026, 4, 3), "Good Friday"),
    (2026, date(2026, 5, 18), "Victoria Day"),
    (2026, date(2026, 7, 1), "Canada Day"),
    (2026, date(2026, 9, 7), "Labour Day"),
    (2026, date(2026, 10, 12), "Thanksgiving"),
    (2026, date(2026, 12, 25), "Christmas Day"),
    (2026, date(2026, 12, 26), "Boxing Day"),
]


def pytest_collection_modifyitems(items):
    """asyncpg connections are loop-bound and session-scoped fixtures hold
    them, so every async test shares the session loop."""
    session_mark = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if isinstance(item, pytest.Function) and asyncio.iscoroutinefunction(item.function):
            item.add_marker(session_mark, append=False)


@pytest.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

        for stmt in (
            "DROP TABLE IF EXISTS role_permission_locks CASCADE",
            "DROP TABLE IF EXISTS role_permissions CASCADE",
            "DROP TABLE IF EXISTS permission_defs CASCADE",
            "DROP TABLE IF EXISTS role_defs CASCADE",
            "DROP TABLE IF EXISTS user_roles CASCADE",
        ):
            await conn.execute(sqlalchemy.text(stmt))
        await conn.execute(sqlalchemy.text(
            "CREATE TABLE role_defs (code varchar(50) PRIMARY KEY, label varchar(100) NOT NULL,"
            " sort integer NOT NULL DEFAULT 0, is_active boolean NOT NULL DEFAULT true,"
            " assignable_as_primary boolean NOT NULL DEFAULT true)"))
        await conn.execute(sqlalchemy.text(
            "CREATE TABLE permission_defs (key varchar(64) PRIMARY KEY, module varchar(20) NOT NULL,"
            " label varchar(120) NOT NULL, sort integer NOT NULL DEFAULT 0)"))
        await conn.execute(sqlalchemy.text(
            "CREATE TABLE role_permissions (role_code varchar(50) NOT NULL,"
            " permission_key varchar(64) NOT NULL, updated_by uuid,"
            " updated_at timestamptz NOT NULL DEFAULT now(),"
            " PRIMARY KEY (role_code, permission_key))"))
        await conn.execute(sqlalchemy.text(
            "CREATE TABLE role_permission_locks (role_code varchar(50) NOT NULL,"
            " permission_key varchar(64) NOT NULL, PRIMARY KEY (role_code, permission_key))"))
        await conn.execute(sqlalchemy.text(
            "CREATE TABLE user_roles (user_id uuid NOT NULL, role_code varchar(50) NOT NULL,"
            " PRIMARY KEY (user_id, role_code))"))

        for i, code in enumerate(ALL_ROLES):
            await conn.execute(sqlalchemy.text(
                "INSERT INTO role_defs (code,label,sort) VALUES (:c,:c,:s)"), {"c": code, "s": i})
        for i, key in enumerate(EHS_KEYS):
            await conn.execute(sqlalchemy.text(
                "INSERT INTO permission_defs (key,module,label,sort) VALUES (:k,'ehs',:k,:s)"),
                {"k": key, "s": i})
        # Reporting goes to every role — the grant the real migration makes.
        for code in ALL_ROLES:
            await conn.execute(sqlalchemy.text(
                "INSERT INTO role_permissions (role_code,permission_key)"
                " VALUES (:r,'ehs.incident.report')"), {"r": code})
        for key, roles in EHS_GRANTS.items():
            for role in roles:
                await conn.execute(sqlalchemy.text(
                    "INSERT INTO role_permissions (role_code,permission_key) VALUES (:r,:k)"),
                    {"r": role, "k": key})

        for year, day, name in TEST_HOLIDAYS:
            await conn.execute(sqlalchemy.text(
                "INSERT INTO ehs_holidays (id,year,holiday_date,name)"
                " VALUES (gen_random_uuid(),:y,:d,:n)"), {"y": year, "d": day, "n": name})

    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def _patch_session_factory(test_engine):
    original = session_module.AsyncSessionLocal
    session_module.AsyncSessionLocal = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    yield
    session_module.AsyncSessionLocal = original


@pytest.fixture
async def db_session(test_engine):
    """One savepoint-isolated session per test, always rolled back."""
    conn = await test_engine.connect()
    trans = await conn.begin()
    session = AsyncSession(
        bind=conn, join_transaction_mode="create_savepoint",
        expire_on_commit=False, autoflush=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()


def make_session_override(session: AsyncSession):
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session
    return _override


async def make_user(test_engine, *, role: str = "worker", full_name: str | None = None) -> User:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    user = User(
        id=uuid.uuid4(),
        full_name=full_name or f"Test {role} {uuid.uuid4().hex[:4]}",
        email=f"{role}-{uuid.uuid4().hex[:8]}@ehs-test.com",
        role=role, is_active=True,
    )
    async with factory() as db:
        db.add(user)
        await db.commit()
    return user


def make_token(user_id: uuid.UUID | str, role: str) -> str:
    return jwt.encode(
        {"sub": str(user_id), "role": role, "type": "access",
         "exp": datetime.now(timezone.utc) + timedelta(hours=8)},
        _JWT_SECRET, algorithm=_JWT_ALG)


def authed_client(token: str, session: AsyncSession | None = None) -> AsyncClient:
    app = create_app()
    if session is not None:
        app.dependency_overrides[get_session] = make_session_override(session)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
async def worker(test_engine, db_session):
    user = await make_user(test_engine, role="worker")
    async with authed_client(make_token(user.id, user.role), db_session) as c:
        yield user, c


@pytest.fixture
async def hse_manager(test_engine, db_session):
    user = await make_user(test_engine, role="ehs_manager")
    async with authed_client(make_token(user.id, user.role), db_session) as c:
        yield user, c


@pytest.fixture
async def auditor(test_engine, db_session):
    user = await make_user(test_engine, role="auditor")
    async with authed_client(make_token(user.id, user.role), db_session) as c:
        yield user, c
