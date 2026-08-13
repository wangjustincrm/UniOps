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
    # Guard against DROP SCHEMA CASCADE ever running against a non-test
    # database. TEST_MRP_DB is env-controlled and this repo has a documented
    # history of a host .env pointing POSTGRES_* at the shared production DB
    # (feedback_uniops_host_env_points_at_prod) — a typo'd/missing override
    # here must fail loudly, not drop whatever database the name resolves
    # to. Mirrors mdm-api's/budget-api's test-db safety convention.
    if not TEST_DB.endswith("_test"):
        raise RuntimeError(
            f"refusing to DROP SCHEMA on database {TEST_DB!r} — TEST_MRP_DB "
            "must end in '_test' (got a name that doesn't look like a test "
            "database; this guard exists because a misconfigured env has "
            "pointed at production before)"
        )
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


@pytest_asyncio.fixture
async def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture
async def non_admin_token():
    """A token for a role that is NOT system_admin (M12, final-phase
    review). `admin_token` short-circuits every `require_permission(...)`
    check before it ever looks at a permission key (see uniops_authz.core's
    system_admin fast path) — a suite that only ever authenticates as
    system_admin would pass all 61 tests even if a gated endpoint's
    permission key were misspelled or swapped for the wrong one, because
    the check is never actually exercised.

    mrp-api's own alembic chain doesn't own identity's role_permissions/
    role_defs/user_roles tables (this fixture's `mrp_test` database never
    has them), so any test using this token to reach a real
    require_permission(...) gate must ALSO monkeypatch
    `uniops_authz.core.user_role_codes`/`_effective_matrix` — see
    tests/test_permission_gates.py's `_deny_everything` helper for the
    idiom (same one mdm-api/tests/test_boms_read_authz.py uses)."""
    return _token(uuid.uuid4(), "requester")
