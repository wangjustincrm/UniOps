"""Shared pytest fixtures for the vms-api test suite.

Mirrors `epms-api/tests/conftest.py` and `expense-api/tests/conftest.py` —
test DB is `vms_test`. Run once before:
    python -m scripts.create_test_db

The vms-api models register a User mirror against `users` (read-only in
production but materialized in the test DB by `Base.metadata.create_all`).
We populate it directly via SQLAlchemy because vms-api does not own the
write path for users — epms-api does.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Register ALL models with Base.metadata before create_all runs.
import app.models  # noqa: F401
import app.db.session as session_module
from app.core.config import settings
from app.db.base import Base
from app.main import create_app
from app.models.user_mirror import User
from app.models.vms_config import VmsConfig

# ── Test DB URL ─────────────────────────────────────────────────────────────-

_base_url, _ = str(settings.DATABASE_URL).rsplit("/", 1)
_TEST_DB_URL = f"{_base_url}/vms_test"

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

    Also seeds the `vms_config` singleton row that the Alembic migration
    would have INSERTed in production. Tests use `Base.metadata.create_all`
    rather than running migrations, so we have to seed it explicitly.
    """
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(VmsConfig(
            quality_manager_user_ids=[],
            notification_contacts={},
            health_questions={},
            badge_templates={},
        ))
        await db.commit()

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


@pytest.fixture(scope="session", autouse=True)
async def _stub_approval_engine():
    """Replace HTTP calls to approval-api with a local no-op.

    In production, vms-api's `services/approval.submit_for_approval` POSTs
    to approval-api over HTTP. Tests can't reach a running approval-api so
    we stub it to:
      - on submit  → flip Visit.approval_status from "draft" to "submitted"
      - on cancel  → flip to "cancelled"
    The actual engine state machine (auto-skip, task creation, post-approve
    callback) is exercised by a separate integration test that runs the
    engine in-process. For everyday unit tests, this stub is enough to
    verify vms-api's side of the contract.
    """
    from app.services import approval as approval_svc
    from sqlalchemy import select as _select
    from app.models.visit import Visit as _Visit

    original_submit = approval_svc.submit_for_approval
    original_cancel = approval_svc.cancel_approval

    async def _stub_submit(visit_id, bearer_token):
        async with session_module.AsyncSessionLocal() as db:
            row = (await db.execute(_select(_Visit).where(_Visit.id == visit_id))).scalar_one_or_none()
            if row is not None and row.approval_status == "draft":
                row.approval_status = "submitted"
                row.approval_step_idx = 0
                await db.commit()
        return {"new_status": "submitted"}

    async def _stub_cancel(visit_id, bearer_token):
        async with session_module.AsyncSessionLocal() as db:
            row = (await db.execute(_select(_Visit).where(_Visit.id == visit_id))).scalar_one_or_none()
            if row is not None and row.approval_status in ("draft", "submitted", "in_review"):
                row.approval_status = "cancelled"
                await db.commit()
        return {"new_status": "cancelled"}

    approval_svc.submit_for_approval = _stub_submit
    approval_svc.cancel_approval = _stub_cancel
    yield
    approval_svc.submit_for_approval = original_submit
    approval_svc.cancel_approval = original_cancel


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
        email=email or f"{role}-{uuid.uuid4().hex[:8]}@vms-test.com",
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


def authed_client(token: str) -> AsyncClient:
    app = create_app()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


# ── Common-role fixtures ────────────────────────────────────────────────────-

@pytest.fixture
async def requester(test_engine):
    """A typical requester + their authenticated client."""
    user = await make_user(test_engine, role="requester")
    token = make_token(user.id, user.role)
    async with authed_client(token) as c:
        yield user, c


@pytest.fixture
async def admin(test_engine):
    user = await make_user(test_engine, role="system_admin")
    token = make_token(user.id, user.role)
    async with authed_client(token) as c:
        yield user, c


@pytest.fixture
async def auditor(test_engine):
    user = await make_user(test_engine, role="auditor")
    token = make_token(user.id, user.role)
    async with authed_client(token) as c:
        yield user, c


@pytest.fixture
async def client():
    """Unauthenticated client."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
