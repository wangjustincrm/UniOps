"""Identity test fixtures — identity_test DB (create_all) + real local Redis.

Mirrors the epms-api conftest pattern: session-scoped engine, patched session
factory, pre-authenticated clients. Redis uses the local docker instance
(localhost:6379) — OTP/blacklist tests exercise the real thing.
"""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.base as db_module
from app.core.security import create_access_token
from app.db.base import Base
from app.main import app
from app.models import audit, authz, config, sod, user  # noqa: F401

TEST_DB = os.getenv("TEST_IDENTITY_DB", "identity_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
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


def pytest_collection_modifyitems(items):
    session_mark = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if isinstance(item, pytest.Function) and asyncio.iscoroutinefunction(item.function):
            item.add_marker(session_mark, append=False)


@pytest.fixture(scope="session")
async def test_engine():
    _ensure_db()
    engine = create_async_engine(ASYNC_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # company-level MFA off by default in tests (missing row ⇒ identity forces
    # MFA on — the secure default); per-user mfa_enabled drives the MFA tests
    from app.models.config import CompanyConfig
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add(CompanyConfig(mfa_enabled=False))
        await s.commit()
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def _patch_session_factory(test_engine):
    original = db_module.AsyncSessionLocal
    db_module.AsyncSessionLocal = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield
    db_module.AsyncSessionLocal = original


@pytest.fixture
async def db_session(test_engine):
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def make_user(test_engine, *, password="TestPass1!", role="requester",
                    mfa_enabled=False) -> "user.User":
    from app.core.security import hash_password
    from app.models.user import User
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as db:
        u = User(
            email=f"u-{uuid.uuid4().hex[:10]}@example.com",
            hashed_password=hash_password(password),
            full_name="Test User", role=role, mfa_enabled=mfa_enabled,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


@pytest.fixture
async def auth_client(test_engine):
    u = await make_user(test_engine)
    token = create_access_token(str(u.id), u.role)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        c.test_user = u
        yield c
