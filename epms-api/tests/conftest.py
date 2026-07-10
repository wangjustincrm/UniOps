"""Shared pytest fixtures for the EPMS API test suite."""
import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — registers all models with Base.metadata
import app.db.session as session_module
from app.core.config import settings
from app.core.security import create_access_token
from app.crud import user as user_crud
from app.db.base import Base
from app.main import create_app
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


# ── Fake mdm-api client ──────────────────────────────────────────────────────────
# Vendor master is owned by mdm-api (B3 / P1): EPMS forwards supplier writes to
# mdm /partners. In tests we stand in for mdm by writing the shared
# business_partners table directly (what mdm would do), so vendor creation works
# without running mdm-api. Set FakeMdmClient.suppliers for ERP-mirror reads.
class FakeMdmClient:
    suppliers: dict[str, dict] = {}

    def __init__(self, bearer_token: str | None = None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_supplier(self, code: str):
        return FakeMdmClient.suppliers.get(code)

    async def get_person(self, code: str):
        return None

    async def find_partner_by_code(self, code: str):
        async with session_module.AsyncSessionLocal() as db:
            v = (await db.execute(select(Vendor).where(Vendor.code == code))).scalar_one_or_none()
            return {"id": str(v.id), "code": v.code} if v else None

    async def create_partner(self, payload: dict) -> dict:
        from app.services.mdm_client import MdmError
        cols = {c.name for c in Vendor.__table__.columns}
        async with session_module.AsyncSessionLocal() as db:
            if (await db.execute(select(Vendor).where(Vendor.code == payload["code"]))).scalar_one_or_none():
                raise MdmError(409, f"Partner code '{payload['code']}' already exists")
            v = Vendor(**{k: val for k, val in payload.items() if k in cols})
            db.add(v)
            await db.commit()
            await db.refresh(v)
            return {"id": str(v.id), "code": v.code, "name": v.name}

    async def update_partner(self, partner_id: str, payload: dict) -> dict:
        from app.services.mdm_client import MdmError
        cols = {c.name for c in Vendor.__table__.columns}
        async with session_module.AsyncSessionLocal() as db:
            v = await db.get(Vendor, uuid.UUID(partner_id))
            if v is None:
                raise MdmError(404, "Partner not found")
            for k, val in payload.items():
                if k in cols:
                    setattr(v, k, val)
            await db.commit()
            return {"id": partner_id}


@pytest.fixture(autouse=True)
def _patch_mdm_client(monkeypatch):
    """Route EPMS vendor-write forwarding to the in-test fake (no real mdm-api)."""
    import app.api.v1.vendors as vendors_api
    monkeypatch.setattr(vendors_api, "MdmClient", FakeMdmClient)
    FakeMdmClient.suppliers = {}
    yield

# ── Test DB ────────────────────────────────────────────────────────────────────
# Requires a pre-created database. Run once:
#   python -m scripts.create_test_db
_base_url, _ = str(settings.DATABASE_URL).rsplit("/", 1)
_TEST_DB_URL = f"{_base_url}/epms_test"


# ── Force all async tests to use the session event loop ───────────────────────
# pytest-asyncio 0.24 defaults tests to per-function loops while session-scoped
# fixtures use a single session loop. asyncpg connections are loop-bound, so
# tests must share the same session loop to avoid "Future attached to different
# loop" errors.

def pytest_collection_modifyitems(items):
    session_mark = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if isinstance(item, pytest.Function) and asyncio.iscoroutinefunction(item.function):
            item.add_marker(session_mark, append=False)


@pytest.fixture(scope="session")
async def test_engine():
    """Create tables once per session; drop them on teardown."""
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def _patch_session_factory(test_engine):
    """
    Redirect AsyncSessionLocal to the test engine for the whole session.
    This makes every HTTP request go to epms_test instead of epms.
    """
    original = session_module.AsyncSessionLocal
    session_module.AsyncSessionLocal = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield
    session_module.AsyncSessionLocal = original


# ── HTTP clients ───────────────────────────────────────────────────────────────

@pytest.fixture
async def client():
    """Unauthenticated HTTPX client pointed at the test DB."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _authenticated_client(test_engine, role: str):
    """Create a user and return a pre-authenticated HTTPX client."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
                password="TestPass1!",
                full_name=f"Test {role}",
                role=role,
            ),
        )
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    app = create_app()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
async def admin_client(test_engine):
    async with await _authenticated_client(test_engine, "system_admin") as c:
        yield c


@pytest.fixture
async def finance_client(test_engine):
    async with await _authenticated_client(test_engine, "finance_manager") as c:
        yield c


@pytest.fixture
async def requester_client(test_engine):
    async with await _authenticated_client(test_engine, "requester") as c:
        yield c
