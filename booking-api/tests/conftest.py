"""Shared pytest fixtures for the booking-api test suite.

Mirrors `epms-api/tests/conftest.py` and `expense-api/tests/conftest.py` —
test DB is `booking_test`. Run once before:
    python -m scripts.create_test_db

The booking-api models register a User mirror against `users` (read-only in
production but materialized in the test DB by `Base.metadata.create_all`).
We populate it directly via SQLAlchemy because booking-api does not own the
write path for users — epms-api does.
"""
import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Register ALL models with Base.metadata before create_all runs.
import app.models  # noqa: F401
import app.db.session as session_module
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models.user_mirror import User

# ── Test DB URL ─────────────────────────────────────────────────────────────-

_base_url, _ = str(settings.DATABASE_URL).rsplit("/", 1)
_TEST_DB_URL = f"{_base_url}/booking_test"

_JWT_SECRET = settings.JWT_SECRET_KEY
_JWT_ALG = settings.JWT_ALGORITHM


# ── pytest-asyncio loop scope ───────────────────────────────────────────────-
# All async tests share the session loop because asyncpg connections are
# loop-bound and session-scoped fixtures hold connections.

def pytest_collection_modifyitems(items):
    session_mark = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if isinstance(item, pytest.Function) and asyncio.iscoroutinefunction(item.function):
            item.add_marker(session_mark, append=False)


# ── Test engine ─────────────────────────────────────────────────────────────-

@pytest.fixture(scope="session")
async def test_engine():
    """Drop + recreate every table at the start of the session.

    After create_all, we also install the btree_gist extension and the
    no_double_booking exclusion constraint, which are SQL-only constructs
    that Base.metadata.create_all() cannot generate.  This mirrors what the
    Alembic migration does in production.
    """
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # Install the exclusion constraint that lives only in migration SQL.
        # asyncpg does not support multiple statements in one execute() call,
        # so each DDL statement is executed separately.
        await conn.execute(
            sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS btree_gist")
        )
        await conn.execute(
            sqlalchemy.text(
                "ALTER TABLE bookings DROP CONSTRAINT IF EXISTS no_double_booking"
            )
        )
        await conn.execute(
            sqlalchemy.text(
                """
                ALTER TABLE bookings ADD CONSTRAINT no_double_booking
                EXCLUDE USING gist (room_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
                WHERE (status = 'confirmed')
                """
            )
        )
        await conn.execute(
            sqlalchemy.text(
                "ALTER TABLE bookings DROP CONSTRAINT IF EXISTS ck_booking_times"
            )
        )
        await conn.execute(
            sqlalchemy.text(
                "ALTER TABLE bookings ADD CONSTRAINT ck_booking_times CHECK (ends_at > starts_at)"
            )
        )

    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def _patch_session_factory(test_engine):
    """Redirect AsyncSessionLocal so every request reaches the test DB."""
    original = session_module.AsyncSessionLocal
    session_module.AsyncSessionLocal = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield
    session_module.AsyncSessionLocal = original


# ── Per-test DB session ─────────────────────────────────────────────────────-

@pytest.fixture
async def db_session(test_engine):
    """Yield a fully-isolated AsyncSession per test, always rolled back on teardown.

    Standard savepoint-isolation pattern:
      1. Open a real connection and begin an outer transaction.
      2. Bind an AsyncSession to it with join_transaction_mode="create_savepoint"
         so every session.begin() issues a SAVEPOINT, not a real BEGIN.
      3. Any IntegrityError raised inside a test only aborts to the savepoint;
         the outer connection stays alive and healthy.
      4. Unconditionally roll back the outer transaction so no data leaks
         between tests, regardless of what the test did.
    """
    conn = await test_engine.connect()
    trans = await conn.begin()
    session = AsyncSession(
        bind=conn,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()


# ── Session override for HTTP integration tests ─────────────────────────────-

def make_session_override(session: AsyncSession):
    """Return a get_session override that yields the given session.

    Binding the HTTP client's app to the same AsyncSession (and thus the same
    connection/savepoint) as the db_session fixture means:
      - Data inserted via db_session is immediately visible to HTTP requests
        without any commit (the session is on the same connection).
      - Everything is rolled back on teardown; no data leaks between tests.
    """
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    return _override


# ── User factory ────────────────────────────────────────────────────────────-

async def make_user(
    test_engine,
    *,
    role: str = "requester",
    department_id: uuid.UUID | None = None,
    full_name: str | None = None,
    email: str | None = None,
) -> User:
    """Insert a row into the (mirrored) users table for FK satisfaction."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    user = User(
        id=uuid.uuid4(),
        full_name=full_name or f"Test {role.title()} {uuid.uuid4().hex[:4]}",
        email=email or f"{role}-{uuid.uuid4().hex[:8]}@booking-test.com",
        role=role,
        department_id=department_id,
        is_active=True,
    )
    async with factory() as db:
        db.add(user)
        await db.commit()
    return user


# ── Token + client helpers ──────────────────────────────────────────────────-

def make_token(user_id: uuid.UUID | str, role: str) -> str:
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            "type": "access",
            "exp": datetime.utcnow() + timedelta(hours=8),
        },
        _JWT_SECRET,
        algorithm=_JWT_ALG,
    )


def authed_client(token: str, session: AsyncSession | None = None) -> AsyncClient:
    """Build an authenticated HTTPX async client against the test app.

    When ``session`` is provided the app's get_session dependency is overridden
    to yield that session, binding the HTTP client to the same DB connection /
    savepoint as the per-test db_session fixture.  This ensures HTTP requests
    see data inserted via db_session without any intermediate commit, and all
    data is rolled back on teardown.
    """
    app = create_app()
    if session is not None:
        app.dependency_overrides[get_session] = make_session_override(session)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


# ── Common-role fixtures ────────────────────────────────────────────────────-

@pytest.fixture
async def requester(test_engine, db_session):
    """A typical requester + their authenticated client.

    The client's app is bound to db_session so HTTP requests participate in
    the same per-test savepoint and see unflushed inserts immediately.
    """
    user = await make_user(test_engine, role="requester")
    token = make_token(user.id, user.role)
    async with authed_client(token, session=db_session) as c:
        yield user, c


@pytest.fixture
async def admin(test_engine, db_session):
    user = await make_user(test_engine, role="system_admin")
    token = make_token(user.id, user.role)
    async with authed_client(token, session=db_session) as c:
        yield user, c


@pytest.fixture
async def auditor(test_engine, db_session):
    user = await make_user(test_engine, role="auditor")
    token = make_token(user.id, user.role)
    async with authed_client(token, session=db_session) as c:
        yield user, c


@pytest.fixture
async def client(db_session):
    """Unauthenticated client, also bound to the per-test db_session."""
    app = create_app()
    app.dependency_overrides[get_session] = make_session_override(db_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
