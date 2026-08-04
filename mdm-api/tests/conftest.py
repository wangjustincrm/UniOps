import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
# Import all models so metadata is populated
from app.models import (  # noqa: F401
    vendor, department, cost_center, part, user, company,
    erp_material, erp_supplier, erp_person, erp_sync_state, tax,
    business_partner, uom, material, uom_conversion, nc_bom,
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; DB-backed tests skipped")
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session):
    """HTTP test client for the FastAPI app, wired to the test db_session and
    an authenticated system_admin user (auth is dependency-overridden — no
    real JWT needed; system_admin short-circuits require_permission too, so
    this exercises endpoint wiring without needing identity's role/permission
    tables, which mdm-api's own alembic chain doesn't own)."""
    from app.main import app
    from app.db.base import get_db
    from app.core.deps import get_token_payload

    async def _override_db():
        yield db_session

    async def _override_user():
        return {"sub": str(uuid.uuid4()), "role": "system_admin", "type": "access"}

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_token_payload] = _override_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
