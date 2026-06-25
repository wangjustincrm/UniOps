# ERP MDM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull ERP material/supplier/person master data into UniOps via manual admin-triggered sync, and let admins selectively import those records into operational users/vendors plus drive PR-type-1 material selection.

**Architecture:**
- `mdm-api` (:8002) owns ERP mirror tables, sync orchestrator, ERP HTTP client, and read-only browse endpoints.
- `epms-api` (:8000) hosts bulk import endpoints that read from mdm-api over HTTP and write to its own `users` / `vendors` tables.
- Portal (and EPMS for vendors/PR) gets a new `mdmApi` HTTP client and new UI sections.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + PostgreSQL 15; Vite + React 18 + TypeScript strict + TanStack Query v5 + Tailwind + shadcn/ui; httpx for service-to-service calls.

**Spec:** [docs/superpowers/specs/2026-05-26-erp-mdm-integration-design.md](../specs/2026-05-26-erp-mdm-integration-design.md)

---

## Phase A — mdm-api ERP foundation

### Task A1: Add ERP config + httpx dependency

**Files:**
- Modify: `mdm-api/app/core/config.py`
- Modify: `mdm-api/requirements.txt`

- [ ] **Step 1: Confirm httpx is already a dep**

Run: `grep -i "^httpx" c:/Project/uniops/mdm-api/requirements.txt`
Expected: a line like `httpx==0.27.0` (or similar). If missing, add `httpx>=0.27,<0.28` to `requirements.txt`.

- [ ] **Step 2: Extend Settings with ERP fields**

Edit `mdm-api/app/core/config.py`:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    service_name: str = "mdm-stub"
    port: int = 8002

    # ERP integration
    erp_base_url: str = "http://10.10.95.66"
    erp_timeout_seconds: float = 60.0

    model_config = {"env_file": ".env"}


settings = Settings()
```

- [ ] **Step 3: Commit**

```bash
git add mdm-api/app/core/config.py mdm-api/requirements.txt
git commit -m "feat(mdm): add ERP_BASE_URL config and httpx dep"
```

---

### Task A2: Create ERP mirror table models

**Files:**
- Create: `mdm-api/app/models/erp_material.py`
- Create: `mdm-api/app/models/erp_supplier.py`
- Create: `mdm-api/app/models/erp_person.py`
- Create: `mdm-api/app/models/erp_sync_state.py`
- Modify: `mdm-api/app/main.py` (import new models)

- [ ] **Step 1: Write `erp_material.py`**

```python
from datetime import datetime
from decimal import Decimal
from sqlalchemy import Boolean, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class ErpMaterial(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "erp_materials"

    erp_part_no: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_meas: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dim_quality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    weight_net: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    weight_gross: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    part_status: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    item_mes_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    erp_rowversion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 2: Write `erp_supplier.py`**

```python
from datetime import datetime
from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class ErpSupplier(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "erp_suppliers"

    erp_supplier_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_tel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supplier_fax: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supplier_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    erp_rowversion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 3: Write `erp_person.py`**

```python
from datetime import datetime
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class ErpPerson(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "erp_persons"

    erp_person_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    person_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pk_psndoc: Mapped[str | None] = mapped_column(String(50), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    erp_rowversion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Write `erp_sync_state.py`**

```python
from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class ErpSyncState(Base):
    __tablename__ = "erp_sync_state"

    kind: Mapped[str] = mapped_column(String(20), primary_key=True)  # 'material' | 'supplier' | 'person'
    last_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 'success' | 'failed' | 'running'
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 5: Register models in `main.py`**

Edit `mdm-api/app/main.py` line 8 — extend the import:

```python
from app.models import vendor, department, cost_center, part, user, company, erp_material, erp_supplier, erp_person, erp_sync_state  # noqa: F401
```

- [ ] **Step 6: Commit**

```bash
git add mdm-api/app/models/erp_*.py mdm-api/app/main.py
git commit -m "feat(mdm): add ERP mirror table models"
```

---

### Task A3: Alembic migration for ERP mirror tables

**Files:**
- Create: `mdm-api/alembic/versions/0002_create_erp_mirrors.py`

- [ ] **Step 1: Identify the current head**

Run: `cd c:/Project/uniops/mdm-api && alembic heads`
Expected: `0001_companies (head)`. If a different revision, substitute it below.

- [ ] **Step 2: Write migration file**

Create `mdm-api/alembic/versions/0002_create_erp_mirrors.py`:

```python
"""create ERP mirror tables

Revision ID: 0002_erp_mirrors
Revises: 0001_companies
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid

revision = "0002_erp_mirrors"
down_revision = "0001_companies"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "erp_materials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("erp_part_no", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("unit_meas", sa.String(20), nullable=True),
        sa.Column("dim_quality", sa.String(100), nullable=True),
        sa.Column("weight_net", sa.Numeric(12, 4), nullable=True),
        sa.Column("weight_gross", sa.Numeric(12, 4), nullable=True),
        sa.Column("volume", sa.Numeric(12, 6), nullable=True),
        sa.Column("part_status", sa.String(10), nullable=True, index=True),
        sa.Column("item_mes_type", sa.String(20), nullable=True, index=True),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("erp_rowversion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "erp_suppliers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("erp_supplier_code", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("supplier_name", sa.String(255), nullable=False),
        sa.Column("supplier_address", sa.Text, nullable=True),
        sa.Column("supplier_tel", sa.String(50), nullable=True),
        sa.Column("supplier_fax", sa.String(50), nullable=True),
        sa.Column("supplier_type", sa.String(20), nullable=True),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("erp_rowversion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "erp_persons",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("erp_person_code", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("person_name", sa.String(255), nullable=False),
        sa.Column("company_code", sa.String(50), nullable=True),
        sa.Column("company_name", sa.String(255), nullable=True),
        sa.Column("department_code", sa.String(50), nullable=True, index=True),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("is_valid", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("pk_psndoc", sa.String(50), nullable=True),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("erp_rowversion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "erp_sync_state",
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("last_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_message", sa.Text, nullable=True),
        sa.Column("last_row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("erp_sync_state")
    op.drop_table("erp_persons")
    op.drop_table("erp_suppliers")
    op.drop_table("erp_materials")
```

- [ ] **Step 3: Run migration against dev DB**

Run: `cd c:/Project/uniops/mdm-api && alembic upgrade head`
Expected: `Running upgrade 0001_companies -> 0002_erp_mirrors, create ERP mirror tables`. No errors.

- [ ] **Step 4: Verify tables exist**

Run: `docker exec -i uniops-mdm-postgres psql -U postgres -d mdm -c '\dt erp_*'`
Expected: list with `erp_materials`, `erp_persons`, `erp_suppliers`, `erp_sync_state`.

(If your DB host/container name differs, substitute. The check is that the four tables exist.)

- [ ] **Step 5: Commit**

```bash
git add mdm-api/alembic/versions/0002_create_erp_mirrors.py
git commit -m "feat(mdm): migration for ERP mirror tables"
```

---

### Task A4: ERP HTTP client

**Files:**
- Create: `mdm-api/app/services/__init__.py` (if missing)
- Create: `mdm-api/app/services/erp_client.py`
- Create: `mdm-api/tests/__init__.py`
- Create: `mdm-api/tests/conftest.py`
- Create: `mdm-api/tests/test_erp_client.py`

- [ ] **Step 1: Create the test scaffolding**

Create `mdm-api/tests/__init__.py` (empty).

Create `mdm-api/tests/conftest.py`:

```python
import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Write the failing test**

Create `mdm-api/tests/test_erp_client.py`:

```python
from datetime import datetime
import pytest
import httpx
from app.services.erp_client import ErpClient, ErpError


@pytest.mark.anyio
async def test_fetch_materials_success_parses_data():
    payload = {
        "code": 200,
        "message": "ok",
        "data": [{"part_NO": "X1", "description": "test", "unit_MEAS": "PCS", "rowversion": "2026-01-01 00:00:00"}],
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport, base_url="http://stub") as http:
        client = ErpClient(http=http)
        records = await client.fetch_materials(datetime(1900, 1, 1))
    assert len(records) == 1
    assert records[0]["part_NO"] == "X1"


@pytest.mark.anyio
async def test_fetch_materials_error_code_raises():
    payload = {"code": 40004, "message": "query failed", "data": []}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport, base_url="http://stub") as http:
        client = ErpClient(http=http)
        with pytest.raises(ErpError) as exc:
            await client.fetch_materials(datetime(1900, 1, 1))
        assert exc.value.code == 40004
        assert "query failed" in str(exc.value)


@pytest.mark.anyio
async def test_fetch_materials_network_error_raises():
    def boom(req):
        raise httpx.ConnectError("dns failure")
    transport = httpx.MockTransport(boom)
    async with httpx.AsyncClient(transport=transport, base_url="http://stub") as http:
        client = ErpClient(http=http)
        with pytest.raises(ErpError):
            await client.fetch_materials(datetime(1900, 1, 1))
```

- [ ] **Step 3: Run test, expect failure**

Run: `cd c:/Project/uniops/mdm-api && pytest tests/test_erp_client.py -v`
Expected: ImportError or "No module named" — module doesn't exist yet.

- [ ] **Step 4: Write the client**

Create `mdm-api/app/services/__init__.py` (empty) if missing.

Create `mdm-api/app/services/erp_client.py`:

```python
"""HTTP client for the external ERP (Firmus) MDM API."""
from __future__ import annotations
from datetime import datetime
from typing import Any
import httpx
from app.core.config import settings


class ErpError(Exception):
    def __init__(self, code: int | None, message: str):
        self.code = code
        super().__init__(f"ERP error code={code}: {message}")


_PATHS = {
    "material": "/firmusData/touch_mdm_mes/material/getMaterialInfo",
    "supplier": "/firmusData/touch_mdm_mes/supplier/getSupplierInfo",
    "person":   "/firmusData/touch_mdm_mes/person/getPersonInfo",
}


def _fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


class ErpClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> "ErpClient":
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=settings.erp_base_url,
                timeout=settings.erp_timeout_seconds,
            )
        return self

    async def __aexit__(self, *exc):
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _post(self, path: str, ts: datetime) -> list[dict[str, Any]]:
        assert self._http is not None, "ErpClient used outside context manager"
        try:
            resp = await self._http.post(path, json={"ts": _fmt_ts(ts)})
        except httpx.HTTPError as e:
            raise ErpError(None, f"network error: {e}") from e
        if resp.status_code >= 500:
            raise ErpError(resp.status_code, f"HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as e:
            raise ErpError(None, f"invalid JSON: {e}") from e
        code = body.get("code")
        if code != 200:
            raise ErpError(code, body.get("message", "unknown ERP error"))
        return list(body.get("data") or [])

    async def fetch_materials(self, ts: datetime) -> list[dict[str, Any]]:
        return await self._post(_PATHS["material"], ts)

    async def fetch_suppliers(self, ts: datetime) -> list[dict[str, Any]]:
        return await self._post(_PATHS["supplier"], ts)

    async def fetch_persons(self, ts: datetime) -> list[dict[str, Any]]:
        return await self._post(_PATHS["person"], ts)
```

- [ ] **Step 5: Run tests, expect pass**

Run: `cd c:/Project/uniops/mdm-api && pytest tests/test_erp_client.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add mdm-api/app/services mdm-api/tests
git commit -m "feat(mdm): ERP HTTP client with unit tests"
```

---

### Task A5: Sync orchestrator service

**Files:**
- Create: `mdm-api/app/services/erp_sync.py`
- Create: `mdm-api/tests/test_erp_sync.py`

- [ ] **Step 1: Write the failing test**

Create `mdm-api/tests/test_erp_sync.py`:

```python
"""Sync orchestrator tests using a stub ErpClient against a real DB session."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.erp_material import ErpMaterial
from app.models.erp_sync_state import ErpSyncState
from app.services.erp_client import ErpError
from app.services.erp_sync import sync_kind


@pytest.fixture
def material_payload():
    return [{
        "part_NO": "M001",
        "description": "Raw milk powder 400g",
        "unit_MEAS": "PCS",
        "dim_QUALITY": "400g*12",
        "weight_NET": "0.0048",
        "weight_GROSS": "0.005935",
        "volume": "0.030012",
        "part_STATUS": "A",
        "itemMESType": "A-10",
        "rowversion": "2026-05-13 02:12:26",
    }]


@pytest.mark.anyio
async def test_sync_material_full_inserts_record(db_session: AsyncSession, material_payload):
    stub = AsyncMock()
    stub.fetch_materials.return_value = material_payload

    result = await sync_kind(db_session, "material", full=True, client=stub)

    assert result["status"] == "success"
    assert result["mode"] == "full"
    assert result["total"] == 1
    assert result["inserted"] == 1

    rows = (await db_session.execute(select(ErpMaterial))).scalars().all()
    assert len(rows) == 1
    assert rows[0].erp_part_no == "M001"
    assert rows[0].part_status == "A"

    state = await db_session.get(ErpSyncState, "material")
    assert state is not None
    assert state.last_status == "success"
    assert state.last_row_count == 1


@pytest.mark.anyio
async def test_sync_material_second_run_updates_existing(db_session: AsyncSession, material_payload):
    stub = AsyncMock()
    stub.fetch_materials.return_value = material_payload
    await sync_kind(db_session, "material", full=True, client=stub)

    updated = [dict(material_payload[0], description="UPDATED")]
    stub.fetch_materials.return_value = updated

    result = await sync_kind(db_session, "material", full=False, client=stub)
    assert result["updated"] == 1
    assert result["inserted"] == 0

    rows = (await db_session.execute(select(ErpMaterial))).scalars().all()
    assert rows[0].description == "UPDATED"


@pytest.mark.anyio
async def test_sync_marks_failed_state_on_erp_error(db_session: AsyncSession):
    stub = AsyncMock()
    stub.fetch_materials.side_effect = ErpError(40004, "boom")

    with pytest.raises(ErpError):
        await sync_kind(db_session, "material", full=True, client=stub)

    state = await db_session.get(ErpSyncState, "material")
    assert state is not None
    assert state.last_status == "failed"
    assert "boom" in (state.last_message or "")
```

- [ ] **Step 2: Add the test DB fixture in conftest**

Edit `mdm-api/tests/conftest.py`:

```python
import asyncio
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
# Import all models so metadata is populated
from app.models import (  # noqa: F401
    vendor, department, cost_center, part, user, company,
    erp_material, erp_supplier, erp_person, erp_sync_state,
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_engine():
    # Use an in-memory or test PG database; SQLite async won't support JSONB,
    # so require a real Postgres test DB via env var.
    import os
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; integration tests skipped")
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()
```

Add `pytest-asyncio>=0.23` and `httpx>=0.27` to `mdm-api/requirements-dev.txt` (create file if missing).

- [ ] **Step 3: Run test, expect fail (import error on `app.services.erp_sync`)**

Run: `cd c:/Project/uniops/mdm-api && TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/mdm_test pytest tests/test_erp_sync.py -v`
Expected: `ImportError` or `ModuleNotFoundError: app.services.erp_sync`.

(Substitute the right test DB URL for your env. If a test PG isn't available, the fixture skips — make sure one is configured.)

- [ ] **Step 4: Write the orchestrator**

Create `mdm-api/app/services/erp_sync.py`:

```python
"""Sync ERP master data into local mirror tables."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.erp_material import ErpMaterial
from app.models.erp_supplier import ErpSupplier
from app.models.erp_person import ErpPerson
from app.models.erp_sync_state import ErpSyncState
from app.services.erp_client import ErpClient, ErpError


_EPOCH = datetime(1900, 1, 1, tzinfo=timezone.utc)


Kind = Literal["material", "supplier", "person"]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if "T" in s else None
    except ValueError:
        return None


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _map_material(rec: dict) -> dict:
    return {
        "erp_part_no": str(rec["part_NO"]),
        "description": rec.get("description"),
        "unit_meas": rec.get("unit_MEAS"),
        "dim_quality": rec.get("dim_QUALITY"),
        "weight_net": _to_decimal(rec.get("weight_NET")),
        "weight_gross": _to_decimal(rec.get("weight_GROSS")),
        "volume": _to_decimal(rec.get("volume")),
        "part_status": rec.get("part_STATUS"),
        "item_mes_type": rec.get("itemMESType"),
        "raw_payload": rec,
        "erp_rowversion": _parse_dt(rec.get("rowversion")),
    }


def _map_supplier(rec: dict) -> dict:
    return {
        "erp_supplier_code": str(rec["suppliercode"]),
        "supplier_name": rec.get("suppliername") or "",
        "supplier_address": rec.get("supplieraddress"),
        "supplier_tel": rec.get("suppliertel"),
        "supplier_fax": rec.get("supplierfax"),
        "supplier_type": rec.get("suppliertype"),
        "raw_payload": rec,
        "erp_rowversion": _parse_dt(rec.get("rowversion")),
    }


def _map_person(rec: dict) -> dict:
    return {
        "erp_person_code": str(rec["personcode"]),
        "person_name": rec.get("personname") or "",
        "company_code": rec.get("companycode"),
        "company_name": rec.get("companyname"),
        "department_code": rec.get("departmentcode"),
        "department_name": rec.get("departmentname"),
        "is_valid": str(rec.get("isvalid", "1")) == "1",
        "pk_psndoc": rec.get("pk_psndoc"),
        "raw_payload": rec,
        "erp_rowversion": _parse_dt(rec.get("modifiedtime") or rec.get("rowversion")),
    }


_KIND_CONFIG: dict[str, dict] = {
    "material": {
        "model": ErpMaterial,
        "unique_col": "erp_part_no",
        "mapper": _map_material,
        "fetch_attr": "fetch_materials",
    },
    "supplier": {
        "model": ErpSupplier,
        "unique_col": "erp_supplier_code",
        "mapper": _map_supplier,
        "fetch_attr": "fetch_suppliers",
    },
    "person": {
        "model": ErpPerson,
        "unique_col": "erp_person_code",
        "mapper": _map_person,
        "fetch_attr": "fetch_persons",
    },
}


async def _write_state(
    db: AsyncSession, *, kind: str, status: str, message: str,
    last_ts: datetime | None, row_count: int,
) -> None:
    now = datetime.now(timezone.utc)
    stmt = pg_insert(ErpSyncState).values(
        kind=kind,
        last_ts=last_ts,
        last_synced_at=now,
        last_status=status,
        last_message=message,
        last_row_count=row_count,
        updated_at=now,
    ).on_conflict_do_update(
        index_elements=["kind"],
        set_=dict(
            last_ts=last_ts,
            last_synced_at=now,
            last_status=status,
            last_message=message,
            last_row_count=row_count,
            updated_at=now,
        ),
    )
    await db.execute(stmt)


async def sync_kind(
    db: AsyncSession,
    kind: Kind,
    *,
    full: bool = False,
    client: ErpClient | None = None,
) -> dict:
    cfg = _KIND_CONFIG[kind]

    # Determine ts
    state = await db.get(ErpSyncState, kind)
    if full or state is None or state.last_ts is None:
        ts = _EPOCH
        mode = "full"
    else:
        ts = state.last_ts
        mode = "incremental"

    # Fetch from ERP
    try:
        if client is None:
            async with ErpClient() as c:
                records = await getattr(c, cfg["fetch_attr"])(ts)
        else:
            records = await getattr(client, cfg["fetch_attr"])(ts)
    except ErpError as e:
        await _write_state(db, kind=kind, status="failed", message=str(e),
                           last_ts=state.last_ts if state else None, row_count=0)
        await db.commit()
        raise

    # Upsert
    model = cfg["model"]
    mapper = cfg["mapper"]
    unique_col = cfg["unique_col"]
    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0
    max_rowversion: datetime | None = None

    for rec in records:
        try:
            row = mapper(rec)
        except KeyError as e:
            continue
        row["synced_at"] = now
        # Determine insert vs update by pre-check (simple + portable)
        exists_q = select(getattr(model, unique_col)).where(
            getattr(model, unique_col) == row[unique_col]
        )
        existed = (await db.execute(exists_q)).scalar_one_or_none() is not None

        stmt = pg_insert(model).values(**row).on_conflict_do_update(
            index_elements=[unique_col],
            set_={k: v for k, v in row.items() if k != unique_col},
        )
        await db.execute(stmt)

        if existed:
            updated += 1
        else:
            inserted += 1

        rv = row.get("erp_rowversion")
        if rv is not None and (max_rowversion is None or rv > max_rowversion):
            max_rowversion = rv

    new_last_ts = max_rowversion or (state.last_ts if state else None) or now
    await _write_state(
        db, kind=kind, status="success", message="ok",
        last_ts=new_last_ts, row_count=len(records),
    )
    await db.commit()

    return {
        "kind": kind,
        "mode": mode,
        "total": len(records),
        "inserted": inserted,
        "updated": updated,
        "last_ts": new_last_ts.isoformat(),
        "status": "success",
        "message": "ok",
    }
```

- [ ] **Step 5: Run tests, expect pass**

Run: `cd c:/Project/uniops/mdm-api && TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/mdm_test pytest tests/test_erp_sync.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add mdm-api/app/services/erp_sync.py mdm-api/tests/conftest.py mdm-api/tests/test_erp_sync.py mdm-api/requirements-dev.txt
git commit -m "feat(mdm): ERP sync orchestrator with upsert and state tracking"
```

---

### Task A6: Pydantic schemas + CRUD helpers

**Files:**
- Create: `mdm-api/app/schemas/erp.py`
- Create: `mdm-api/app/crud/erp.py`

- [ ] **Step 1: Write `schemas/erp.py`**

```python
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel


class ErpMaterialResponse(BaseModel):
    erp_part_no: str
    description: str | None
    unit_meas: str | None
    dim_quality: str | None
    weight_net: Decimal | None
    weight_gross: Decimal | None
    volume: Decimal | None
    part_status: str | None
    item_mes_type: str | None
    erp_rowversion: datetime | None
    synced_at: datetime

    model_config = {"from_attributes": True}


class ErpSupplierResponse(BaseModel):
    erp_supplier_code: str
    supplier_name: str
    supplier_address: str | None
    supplier_tel: str | None
    supplier_fax: str | None
    supplier_type: str | None
    erp_rowversion: datetime | None
    synced_at: datetime

    model_config = {"from_attributes": True}


class ErpPersonResponse(BaseModel):
    erp_person_code: str
    person_name: str
    company_code: str | None
    company_name: str | None
    department_code: str | None
    department_name: str | None
    is_valid: bool
    pk_psndoc: str | None
    erp_rowversion: datetime | None
    synced_at: datetime

    model_config = {"from_attributes": True}


class ErpSyncStateResponse(BaseModel):
    kind: str
    last_ts: datetime | None
    last_synced_at: datetime | None
    last_status: str | None
    last_message: str | None
    last_row_count: int

    model_config = {"from_attributes": True}


class ErpSyncResultResponse(BaseModel):
    kind: str
    mode: str
    total: int
    inserted: int
    updated: int
    last_ts: str
    status: str
    message: str


class ErpMaterialListResponse(BaseModel):
    items: list[ErpMaterialResponse]
    total: int


class ErpSupplierListResponse(BaseModel):
    items: list[ErpSupplierResponse]
    total: int


class ErpPersonListResponse(BaseModel):
    items: list[ErpPersonResponse]
    total: int
```

- [ ] **Step 2: Write `crud/erp.py`**

```python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.erp_material import ErpMaterial
from app.models.erp_supplier import ErpSupplier
from app.models.erp_person import ErpPerson


async def list_materials(
    db: AsyncSession, *, search: str | None = None, part_status: str | None = None,
    item_mes_type: str | None = None, page: int = 1, page_size: int = 20,
) -> tuple[list[ErpMaterial], int]:
    q = select(ErpMaterial)
    if search:
        term = f"%{search}%"
        q = q.where(ErpMaterial.erp_part_no.ilike(term) | ErpMaterial.description.ilike(term))
    if part_status:
        q = q.where(ErpMaterial.part_status == part_status)
    if item_mes_type:
        q = q.where(ErpMaterial.item_mes_type == item_mes_type)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(
        q.order_by(ErpMaterial.erp_part_no).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def get_material_by_code(db: AsyncSession, code: str) -> ErpMaterial | None:
    return (await db.execute(
        select(ErpMaterial).where(ErpMaterial.erp_part_no == code)
    )).scalar_one_or_none()


async def list_suppliers(
    db: AsyncSession, *, search: str | None = None, exclude_codes: list[str] | None = None,
    page: int = 1, page_size: int = 20,
) -> tuple[list[ErpSupplier], int]:
    q = select(ErpSupplier)
    if search:
        term = f"%{search}%"
        q = q.where(
            ErpSupplier.erp_supplier_code.ilike(term) | ErpSupplier.supplier_name.ilike(term)
        )
    if exclude_codes:
        q = q.where(~ErpSupplier.erp_supplier_code.in_(exclude_codes))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(
        q.order_by(ErpSupplier.supplier_name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def get_supplier_by_code(db: AsyncSession, code: str) -> ErpSupplier | None:
    return (await db.execute(
        select(ErpSupplier).where(ErpSupplier.erp_supplier_code == code)
    )).scalar_one_or_none()


async def list_persons(
    db: AsyncSession, *, search: str | None = None, exclude_codes: list[str] | None = None,
    page: int = 1, page_size: int = 20,
) -> tuple[list[ErpPerson], int]:
    q = select(ErpPerson)
    if search:
        term = f"%{search}%"
        q = q.where(
            ErpPerson.erp_person_code.ilike(term) | ErpPerson.person_name.ilike(term)
        )
    if exclude_codes:
        q = q.where(~ErpPerson.erp_person_code.in_(exclude_codes))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(
        q.order_by(ErpPerson.person_name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def get_person_by_code(db: AsyncSession, code: str) -> ErpPerson | None:
    return (await db.execute(
        select(ErpPerson).where(ErpPerson.erp_person_code == code)
    )).scalar_one_or_none()
```

- [ ] **Step 3: Commit**

```bash
git add mdm-api/app/schemas/erp.py mdm-api/app/crud/erp.py
git commit -m "feat(mdm): pydantic schemas + CRUD for ERP mirrors"
```

---

### Task A7: REST endpoints for sync + browse + by-code lookups

**Files:**
- Create: `mdm-api/app/api/v1/erp_mdm.py`
- Modify: `mdm-api/app/api/v1/__init__.py`
- Create: `mdm-api/tests/test_erp_endpoints.py`

- [ ] **Step 1: Inspect existing auth dep**

Run: `grep -n "CurrentUser\|require_roles\|def get_current" c:/Project/uniops/mdm-api/app/core/deps.py`
Expected output: identifies the auth dep names. Use whichever exists; if `CurrentUser` is the only one, use it. If a role-checking helper exists, use it; otherwise check `current_user.role` inline.

- [ ] **Step 2: Inspect existing router registration**

Read `mdm-api/app/api/v1/__init__.py` to see how the existing routers are included; mirror the pattern.

- [ ] **Step 3: Write the endpoints**

Create `mdm-api/app/api/v1/erp_mdm.py`:

```python
"""ERP MDM endpoints: trigger sync, browse mirrors, by-code lookups."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud import erp as erp_crud
from app.models.erp_sync_state import ErpSyncState
from app.schemas.erp import (
    ErpMaterialListResponse, ErpMaterialResponse,
    ErpSupplierListResponse, ErpSupplierResponse,
    ErpPersonListResponse, ErpPersonResponse,
    ErpSyncStateResponse, ErpSyncResultResponse,
)
from app.services.erp_client import ErpError
from app.services.erp_sync import sync_kind

router = APIRouter(prefix="/erp", tags=["erp-mdm"])


def _require_admin(user) -> None:
    if getattr(user, "role", None) != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin required")


def _require_admin_or(user, *roles: str) -> None:
    role = getattr(user, "role", None)
    if role != "system_admin" and role not in roles:
        raise HTTPException(status_code=403, detail=f"requires one of: system_admin, {','.join(roles)}")


@router.post("/sync/{kind}", response_model=ErpSyncResultResponse)
async def trigger_sync(
    kind: str,
    full: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
):
    _require_admin(user)
    if kind not in ("material", "supplier", "person"):
        raise HTTPException(status_code=400, detail="kind must be material|supplier|person")
    try:
        result = await sync_kind(db, kind, full=full)
    except ErpError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


@router.get("/sync/status")
async def sync_status(db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    _require_admin(user)
    from sqlalchemy import select
    rows = (await db.execute(select(ErpSyncState))).scalars().all()
    out: dict[str, dict | None] = {"material": None, "supplier": None, "person": None}
    for r in rows:
        out[r.kind] = ErpSyncStateResponse.model_validate(r).model_dump()
    return out


@router.get("/materials", response_model=ErpMaterialListResponse)
async def list_materials(
    search: str | None = Query(default=None),
    part_status: str | None = Query(default=None),
    item_mes_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
):
    items, total = await erp_crud.list_materials(
        db, search=search, part_status=part_status, item_mes_type=item_mes_type,
        page=page, page_size=page_size,
    )
    return {"items": items, "total": total}


@router.get("/materials/{code}", response_model=ErpMaterialResponse)
async def get_material(code: str, db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    m = await erp_crud.get_material_by_code(db, code)
    if not m:
        raise HTTPException(status_code=404, detail="material not found")
    return m


@router.get("/suppliers", response_model=ErpSupplierListResponse)
async def list_suppliers(
    search: str | None = Query(default=None),
    exclude_codes: str | None = Query(default=None, description="comma-separated codes to exclude"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
):
    _require_admin_or(user, "vendor_manager")
    excludes = [c.strip() for c in exclude_codes.split(",")] if exclude_codes else None
    items, total = await erp_crud.list_suppliers(
        db, search=search, exclude_codes=excludes, page=page, page_size=page_size,
    )
    return {"items": items, "total": total}


@router.get("/suppliers/{code}", response_model=ErpSupplierResponse)
async def get_supplier(code: str, db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    _require_admin_or(user, "vendor_manager")
    s = await erp_crud.get_supplier_by_code(db, code)
    if not s:
        raise HTTPException(status_code=404, detail="supplier not found")
    return s


@router.get("/persons", response_model=ErpPersonListResponse)
async def list_persons(
    search: str | None = Query(default=None),
    exclude_codes: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
):
    _require_admin(user)
    excludes = [c.strip() for c in exclude_codes.split(",")] if exclude_codes else None
    items, total = await erp_crud.list_persons(
        db, search=search, exclude_codes=excludes, page=page, page_size=page_size,
    )
    return {"items": items, "total": total}


@router.get("/persons/{code}", response_model=ErpPersonResponse)
async def get_person(code: str, db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    _require_admin(user)
    p = await erp_crud.get_person_by_code(db, code)
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    return p
```

- [ ] **Step 4: Register router**

Edit `mdm-api/app/api/v1/__init__.py` — add `from .erp_mdm import router as erp_router` and `api_router.include_router(erp_router)` following the existing pattern.

- [ ] **Step 5: Smoke test the router compiles**

Run: `cd c:/Project/uniops/mdm-api && python -c "from app.api.v1 import api_router; print([r.path for r in api_router.routes])"`
Expected: list of routes including `/erp/sync/{kind}`, `/erp/materials`, etc.

- [ ] **Step 6: Commit**

```bash
git add mdm-api/app/api/v1/erp_mdm.py mdm-api/app/api/v1/__init__.py
git commit -m "feat(mdm): REST endpoints for ERP sync and mirror browse"
```

---

## Phase B — epms-api: import endpoints + schema

### Task B1: Add MDM_API_URL config + httpx client

**Files:**
- Modify: `epms-api/app/core/config.py`
- Create: `epms-api/app/services/mdm_client.py`

- [ ] **Step 1: Add settings**

In `epms-api/app/core/config.py`, add to `Settings`:

```python
    mdm_api_url: str = "http://mdm-api:8002"
    mdm_api_timeout_seconds: float = 30.0
```

- [ ] **Step 2: Write the MDM HTTP client**

Create `epms-api/app/services/mdm_client.py`:

```python
"""Thin httpx client used by epms-api to read ERP mirror data from mdm-api."""
from __future__ import annotations
from typing import Any
import httpx
from app.core.config import settings


class MdmError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        super().__init__(f"mdm-api error {status}: {detail}")


class MdmClient:
    def __init__(self, bearer_token: str):
        self._bearer = bearer_token
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MdmClient":
        self._http = httpx.AsyncClient(
            base_url=f"{settings.mdm_api_url}/mdm/v1",
            timeout=settings.mdm_api_timeout_seconds,
            headers={"Authorization": f"Bearer {self._bearer}"},
        )
        return self

    async def __aexit__(self, *exc):
        if self._http:
            await self._http.aclose()

    async def _get(self, path: str) -> Any:
        assert self._http is not None
        try:
            resp = await self._http.get(path)
        except httpx.HTTPError as e:
            raise MdmError(0, f"network: {e}") from e
        if resp.status_code == 404:
            return None
        if not resp.is_success:
            raise MdmError(resp.status_code, resp.text[:200])
        return resp.json()

    async def get_person(self, code: str) -> dict | None:
        return await self._get(f"/erp/persons/{code}")

    async def get_supplier(self, code: str) -> dict | None:
        return await self._get(f"/erp/suppliers/{code}")
```

- [ ] **Step 3: Commit**

```bash
git add epms-api/app/core/config.py epms-api/app/services/mdm_client.py
git commit -m "feat(epms-api): add MDM client config and httpx wrapper"
```

---

### Task B2: Add `erp_person_code` column to `users`

**Files:**
- Modify: `epms-api/app/models/user.py`
- Modify: `epms-api/app/schemas/user.py`
- Create: `epms-api/alembic/versions/s9n0o1p2q3r4_user_erp_person_code.py`

- [ ] **Step 1: Add column to model**

Edit `epms-api/app/models/user.py` — append a new column after `notification_channel`:

```python
    # Link to ERP person when this user was imported from ERP MDM
    erp_person_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
```

- [ ] **Step 2: Find the current alembic head**

Run: `cd c:/Project/uniops/epms-api && alembic heads`
Expected: a single revision id (likely `r8m9n0o1p2q3` per existing files). Substitute below.

- [ ] **Step 3: Create migration**

Create `epms-api/alembic/versions/s9n0o1p2q3r4_user_erp_person_code.py`:

```python
"""add erp_person_code to users + vendors erp_id partial unique

Revision ID: s9n0o1p2q3r4
Revises: r8m9n0o1p2q3
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa


revision = "s9n0o1p2q3r4"
down_revision = "r8m9n0o1p2q3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("erp_person_code", sa.String(50), nullable=True))
    op.create_index(
        "ux_users_erp_person_code",
        "users",
        ["erp_person_code"],
        unique=True,
        postgresql_where=sa.text("erp_person_code IS NOT NULL"),
    )
    op.create_index(
        "ux_vendors_erp_id",
        "vendors",
        ["erp_id"],
        unique=True,
        postgresql_where=sa.text("erp_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("ux_vendors_erp_id", table_name="vendors")
    op.drop_index("ux_users_erp_person_code", table_name="users")
    op.drop_column("users", "erp_person_code")
```

- [ ] **Step 4: Run migration**

Run: `cd c:/Project/uniops/epms-api && alembic upgrade head`
Expected: `Running upgrade r8m9n0o1p2q3 -> s9n0o1p2q3r4`. No errors.

- [ ] **Step 5: Expose field in UserAdminResponse**

Edit `epms-api/app/schemas/user.py`: add `erp_person_code: str | None = None` to `UserAdminResponse` (and to `UserUpdate` if you want it editable — optional).

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/models/user.py epms-api/app/schemas/user.py epms-api/alembic/versions/s9n0o1p2q3r4_user_erp_person_code.py
git commit -m "feat(epms-api): erp_person_code on users + erp_id unique index on vendors"
```

---

### Task B3: `/users/import-from-erp` endpoint

**Files:**
- Modify: `epms-api/app/api/v1/users.py`
- Modify: `epms-api/app/schemas/user.py`
- Create: `epms-api/tests/test_users_import_from_erp.py`

- [ ] **Step 1: Add request/response schemas**

Edit `epms-api/app/schemas/user.py` — append:

```python
class ErpImportItem(BaseModel):
    erp_person_code: str
    email: str
    role: str | None = None
    department_id: uuid.UUID | None = None
    is_active: bool = True

    @field_validator("email")
    @classmethod
    def _email_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("email is required")
        return v


class ErpImportRequest(BaseModel):
    items: list[ErpImportItem]


class ErpImportCreated(BaseModel):
    email: str
    full_name: str
    temp_password: str


class ErpImportError(BaseModel):
    erp_person_code: str
    reason: str


class ErpImportResponse(BaseModel):
    created: list[ErpImportCreated]
    errors: list[ErpImportError]
```

(Add `from pydantic import BaseModel, field_validator` and `import uuid` to imports if missing.)

- [ ] **Step 2: Write the failing test**

Create `epms-api/tests/test_users_import_from_erp.py`:

```python
"""Test the import-from-erp users endpoint with a stubbed mdm-api client."""
from unittest.mock import AsyncMock, patch
import pytest


@pytest.mark.anyio
async def test_import_from_erp_creates_user(client_admin, db_session):
    stub = AsyncMock()
    stub.get_person.return_value = {
        "erp_person_code": "100100",
        "person_name": "Ayan Asim",
        "department_code": None,
        "department_name": "Production",
        "company_code": "10024",
        "company_name": "Canada Royal Milk ULC",
        "is_valid": True,
    }
    with patch("app.api.v1.users.MdmClient", return_value=AsyncMock(__aenter__=AsyncMock(return_value=stub), __aexit__=AsyncMock())):
        resp = await client_admin.post(
            "/api/v1/users/import-from-erp",
            json={"items": [{"erp_person_code": "100100", "email": "ayan@royalmilk.com"}]},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["created"]) == 1
    assert data["created"][0]["email"] == "ayan@royalmilk.com"
    assert len(data["created"][0]["temp_password"]) >= 12
    assert data["errors"] == []


@pytest.mark.anyio
async def test_import_from_erp_404_when_person_missing(client_admin):
    stub = AsyncMock()
    stub.get_person.return_value = None
    with patch("app.api.v1.users.MdmClient", return_value=AsyncMock(__aenter__=AsyncMock(return_value=stub), __aexit__=AsyncMock())):
        resp = await client_admin.post(
            "/api/v1/users/import-from-erp",
            json={"items": [{"erp_person_code": "999999", "email": "x@royalmilk.com"}]},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == []
    assert data["errors"][0]["erp_person_code"] == "999999"
    assert "not found" in data["errors"][0]["reason"].lower()


@pytest.mark.anyio
async def test_import_from_erp_rejects_empty_email(client_admin):
    resp = await client_admin.post(
        "/api/v1/users/import-from-erp",
        json={"items": [{"erp_person_code": "100100", "email": ""}]},
    )
    assert resp.status_code == 422
```

(Adjust fixture names `client_admin` and `db_session` to match `epms-api/tests/conftest.py` patterns — read it first.)

- [ ] **Step 3: Run test, expect failure**

Run: `cd c:/Project/uniops/epms-api && pytest tests/test_users_import_from_erp.py -v`
Expected: route doesn't exist → 404, or import error.

- [ ] **Step 4: Implement the endpoint**

Append to `epms-api/app/api/v1/users.py`:

```python
from app.services.mdm_client import MdmClient
from app.crud import department as dept_crud
from app.schemas.user import (
    ErpImportRequest, ErpImportResponse, ErpImportCreated, ErpImportError,
)


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization.split(None, 1)[1]


@router.post("/import-from-erp", response_model=ErpImportResponse)
async def import_users_from_erp(
    body: ErpImportRequest,
    db: SessionDep,
    _: AdminDep,
    authorization: str | None = Header(default=None),
):
    bearer = _extract_bearer(authorization)
    created: list[ErpImportCreated] = []
    errors: list[ErpImportError] = []

    async with MdmClient(bearer_token=bearer) as mdm:
        for item in body.items:
            try:
                person = await mdm.get_person(item.erp_person_code)
            except Exception as e:
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason=f"mdm error: {e}"))
                continue
            if not person:
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason="ERP person not found in mdm-api mirror"))
                continue

            # Validate role
            role = item.role or "requester"
            if role not in VALID_ROLES:
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason=f"invalid role: {role}"))
                continue

            # Resolve department
            dept_id = item.department_id
            if dept_id is None and person.get("department_code"):
                dept = await dept_crud.get_by_code(db, person["department_code"])
                if dept:
                    dept_id = dept.id

            # Check uniqueness
            if await user_crud.get_by_email(db, item.email):
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason=f"email already exists: {item.email}"))
                continue
            existing_erp = await user_crud.get_by_erp_person_code(db, item.erp_person_code)
            if existing_erp:
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason="already imported"))
                continue

            temp_pw = secrets.token_urlsafe(12)
            user = User(
                email=item.email.strip().lower(),
                hashed_password=hash_password(temp_pw),
                full_name=person.get("person_name") or item.email,
                role=role,
                department_id=dept_id,
                is_active=item.is_active,
                must_change_password=True,
                erp_person_code=item.erp_person_code,
            )
            db.add(user)
            try:
                await db.flush()
            except Exception as e:
                await db.rollback()
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason=f"db error: {e}"))
                continue
            created.append(ErpImportCreated(
                email=user.email, full_name=user.full_name, temp_password=temp_pw,
            ))

        await db.commit()

    return ErpImportResponse(created=created, errors=errors)
```

Add the necessary imports at the top of `users.py`: `from fastapi import Header`. Make sure `MdmClient` is imported lazily inside the function OR at top — tests patch `app.api.v1.users.MdmClient` so it must be importable at module level.

- [ ] **Step 5: Add `get_by_erp_person_code` to user CRUD**

Edit `epms-api/app/crud/user.py` — add:

```python
async def get_by_erp_person_code(db, code: str):
    from sqlalchemy import select
    from app.models.user import User
    return (await db.execute(select(User).where(User.erp_person_code == code))).scalar_one_or_none()
```

- [ ] **Step 6: Run test, expect pass**

Run: `cd c:/Project/uniops/epms-api && pytest tests/test_users_import_from_erp.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add epms-api/app/api/v1/users.py epms-api/app/crud/user.py epms-api/app/schemas/user.py epms-api/tests/test_users_import_from_erp.py
git commit -m "feat(epms-api): users/import-from-erp endpoint"
```

---

### Task B4: `/vendors/import-from-erp` endpoint

**Files:**
- Modify: `epms-api/app/api/v1/vendors.py`
- Modify: `epms-api/app/schemas/vendor.py`
- Create: `epms-api/tests/test_vendors_import_from_erp.py`

- [ ] **Step 1: Add request/response schemas**

Edit `epms-api/app/schemas/vendor.py` — append:

```python
class ErpVendorImportDefaults(BaseModel):
    category: str = "other"
    payment_terms: str = "net30"
    currency: str = "CAD"


class ErpVendorImportRequest(BaseModel):
    erp_supplier_codes: list[str]
    defaults: ErpVendorImportDefaults = ErpVendorImportDefaults()


class ErpVendorImportError(BaseModel):
    erp_supplier_code: str
    reason: str


class ErpVendorImportResponse(BaseModel):
    created: int
    errors: list[ErpVendorImportError]
```

- [ ] **Step 2: Write the failing test**

Create `epms-api/tests/test_vendors_import_from_erp.py`:

```python
from unittest.mock import AsyncMock, patch
import pytest


@pytest.mark.anyio
async def test_import_vendor_from_erp_creates_vendor(client_admin):
    stub = AsyncMock()
    stub.get_supplier.return_value = {
        "erp_supplier_code": "CRM027",
        "supplier_name": "JiangSu Debang Duoling",
        "supplier_address": "Lianyungang, Jiangsu",
        "supplier_tel": "+86-xxx",
        "supplier_fax": None,
        "supplier_type": "YL",
    }
    with patch("app.api.v1.vendors.MdmClient", return_value=AsyncMock(__aenter__=AsyncMock(return_value=stub), __aexit__=AsyncMock())):
        resp = await client_admin.post(
            "/api/v1/vendors/import-from-erp",
            json={"erp_supplier_codes": ["CRM027"], "defaults": {"category": "ingredients", "payment_terms": "net60", "currency": "USD"}},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 1
    assert data["errors"] == []

    # Verify written
    list_resp = await client_admin.get("/api/v1/vendors?search=CRM027")
    items = list_resp.json()["items"]
    assert any(v["erp_id"] == "CRM027" for v in items)


@pytest.mark.anyio
async def test_import_vendor_skips_duplicate(client_admin):
    stub = AsyncMock()
    stub.get_supplier.return_value = {
        "erp_supplier_code": "CRM027",
        "supplier_name": "X",
    }
    with patch("app.api.v1.vendors.MdmClient", return_value=AsyncMock(__aenter__=AsyncMock(return_value=stub), __aexit__=AsyncMock())):
        await client_admin.post("/api/v1/vendors/import-from-erp",
            json={"erp_supplier_codes": ["CRM027"]})
        resp = await client_admin.post("/api/v1/vendors/import-from-erp",
            json={"erp_supplier_codes": ["CRM027"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 0
    assert len(data["errors"]) == 1
    assert "already" in data["errors"][0]["reason"].lower() or "exists" in data["errors"][0]["reason"].lower()
```

- [ ] **Step 3: Run test, expect fail**

Run: `cd c:/Project/uniops/epms-api && pytest tests/test_vendors_import_from_erp.py -v`
Expected: 404 (route missing).

- [ ] **Step 4: Implement the endpoint**

Append to `epms-api/app/api/v1/vendors.py`:

```python
from fastapi import Header
from app.services.mdm_client import MdmClient
from app.schemas.vendor import (
    ErpVendorImportRequest, ErpVendorImportResponse, ErpVendorImportError,
)


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization.split(None, 1)[1]


@router.post("/import-from-erp", response_model=ErpVendorImportResponse)
async def import_vendors_from_erp(
    body: ErpVendorImportRequest,
    db: SessionDep,
    _: WriteDep,
    authorization: str | None = Header(default=None),
):
    bearer = _extract_bearer(authorization)
    created = 0
    errors: list[ErpVendorImportError] = []

    async with MdmClient(bearer_token=bearer) as mdm:
        for code in body.erp_supplier_codes:
            try:
                supplier = await mdm.get_supplier(code)
            except Exception as e:
                errors.append(ErpVendorImportError(erp_supplier_code=code, reason=f"mdm error: {e}"))
                continue
            if not supplier:
                errors.append(ErpVendorImportError(erp_supplier_code=code, reason="ERP supplier not in mdm-api mirror"))
                continue

            if await vendor_crud.get_by_erp_id(db, code) or await vendor_crud.get_by_code(db, code):
                errors.append(ErpVendorImportError(erp_supplier_code=code, reason=f"vendor already exists for code={code}"))
                continue

            v = Vendor(
                code=code,
                erp_id=code,
                name=supplier.get("supplier_name") or code,
                category=body.defaults.category,
                contact_name=supplier.get("supplier_name") or code,
                contact_email=f"{code}@erp.local",
                phone=supplier.get("supplier_tel"),
                address=supplier.get("supplier_address"),
                payment_terms=body.defaults.payment_terms,
                currency=body.defaults.currency,
                is_active=True,
            )
            db.add(v)
            try:
                await db.flush()
                created += 1
            except Exception as e:
                await db.rollback()
                errors.append(ErpVendorImportError(erp_supplier_code=code, reason=f"db error: {e}"))
                continue

        await db.commit()

    return ErpVendorImportResponse(created=created, errors=errors)
```

(Confirm `WriteDep` is the existing dependency in this file — it's used by the existing `create_vendor`. If named differently, match the existing pattern.)

- [ ] **Step 5: Add `get_by_erp_id` and `get_by_code` to vendor CRUD if missing**

Run: `grep -n "def get_by_erp_id\|def get_by_code" c:/Project/uniops/epms-api/app/crud/vendor.py`
If missing, append to that file:

```python
async def get_by_erp_id(db, erp_id: str):
    from sqlalchemy import select
    from app.models.vendor import Vendor
    return (await db.execute(select(Vendor).where(Vendor.erp_id == erp_id))).scalar_one_or_none()


async def get_by_code(db, code: str):
    from sqlalchemy import select
    from app.models.vendor import Vendor
    return (await db.execute(select(Vendor).where(Vendor.code == code))).scalar_one_or_none()
```

- [ ] **Step 6: Run test, expect pass**

Run: `cd c:/Project/uniops/epms-api && pytest tests/test_vendors_import_from_erp.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add epms-api/app/api/v1/vendors.py epms-api/app/crud/vendor.py epms-api/app/schemas/vendor.py epms-api/tests/test_vendors_import_from_erp.py
git commit -m "feat(epms-api): vendors/import-from-erp endpoint"
```

---

## Phase C — Portal frontend: ERP MDM section + User import

### Task C1: Add `mdmApi` HTTP client to Portal

**Files:**
- Modify: `portal/src/lib/api.ts`

- [ ] **Step 1: Add the new env constant + client**

Edit `portal/src/lib/api.ts` near other `*_API` consts:

```ts
const MDM_API = (import.meta.env.VITE_MDM_API_URL as string | undefined) || 'http://localhost:8002'
```

Add at the end of the file:

```ts
async function mdmRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${MDM_API}/mdm/v1${path}`, {
    method,
    headers: authHeaders(!!body),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    if (getToken()) {
      globalSignOut()
      throw new Error('Session expired. Please log in again.')
    }
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(errBody.detail, res.status))
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T
  return res.json()
}

export const mdmApi = {
  get:    <T>(path: string)                  => mdmRequest<T>('GET',    path),
  post:   <T>(path: string, body?: unknown)  => mdmRequest<T>('POST',   path, body),
  patch:  <T>(path: string, body?: unknown)  => mdmRequest<T>('PATCH',  path, body),
  delete: <T>(path: string)                  => mdmRequest<T>('DELETE', path),
}
```

- [ ] **Step 2: Smoke-build**

Run: `cd c:/Project/uniops/portal && npm run build`
Expected: builds without errors.

- [ ] **Step 3: Commit**

```bash
git add portal/src/lib/api.ts
git commit -m "feat(portal): add mdmApi HTTP client"
```

---

### Task C2: AdminPanel "ERP MDM" sidebar entry + section shell

**Files:**
- Modify: `portal/src/pages/admin/AdminPanel.tsx`

- [ ] **Step 1: Add the sidebar item and section shell**

In `portal/src/pages/admin/AdminPanel.tsx`:

(a) Extend imports at the top:
```ts
import { Database } from 'lucide-react'
import { mdmApi } from '@/lib/api'
```

(b) Append to `SECTIONS`:
```ts
  { key: 'erp_mdm',      label: 'ERP MDM',              icon: Database },
```

(c) Add to the section switch in `<main>`:
```tsx
  {section === 'erp_mdm'     && <ErpMdmSection />}
```

(d) Append a new component above `SECTIONS`:

```tsx
// ── ERP MDM ───────────────────────────────────────────────────────────────────

type ErpKind = 'material' | 'supplier' | 'person'

interface ErpSyncState {
  kind: string
  last_ts: string | null
  last_synced_at: string | null
  last_status: string | null
  last_message: string | null
  last_row_count: number
}

interface ErpSyncResult {
  kind: string; mode: string; total: number; inserted: number; updated: number
  last_ts: string; status: string; message: string
}

function ErpMdmSection() {
  const [tab, setTab] = useState<ErpKind>('material')
  return (
    <div>
      <SectionHeader title="ERP MDM" description="Mirror of ERP master data. Sync manually, then import into Users/Vendors or use in PR Type 1." />
      <div className="mb-4 flex gap-1 border-b border-neutral-200">
        {(['material','supplier','person'] as ErpKind[]).map(k => (
          <button key={k} onClick={() => setTab(k)}
            className={cn('px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              tab === k ? 'border-[#085E5E] text-[#085E5E]' : 'border-transparent text-neutral-500 hover:text-neutral-700')}>
            {k === 'material' ? 'Materials' : k === 'supplier' ? 'Suppliers' : 'Persons'}
          </button>
        ))}
      </div>
      {tab === 'material' && <ErpMaterialsTab />}
      {tab === 'supplier' && <ErpSuppliersTab />}
      {tab === 'person'   && <ErpPersonsTab />}
    </div>
  )
}

function ErpSyncToolbar({ kind, onSearchChange, search, filters }: {
  kind: ErpKind, onSearchChange: (v: string) => void, search: string, filters?: React.ReactNode
}) {
  const qc = useQueryClient()
  const status = useQuery<Record<string, ErpSyncState | null>>({
    queryKey: ['erp-sync-status'],
    queryFn: () => mdmApi.get('/erp/sync/status'),
    refetchInterval: 10_000,
  })
  const s = status.data?.[kind]
  const [syncing, setSyncing] = useState<'inc' | 'full' | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const trigger = async (full: boolean) => {
    if (full && !confirm('Pull ALL records from ERP from the beginning. This may take several minutes. Continue?')) return
    setSyncing(full ? 'full' : 'inc')
    try {
      const r = await mdmApi.post<ErpSyncResult>(`/erp/sync/${kind}?full=${full}`)
      setToast(`Sync ok — ${r.total} records (${r.inserted} inserted, ${r.updated} updated)`)
      qc.invalidateQueries({ queryKey: ['erp-sync-status'] })
      qc.invalidateQueries({ queryKey: ['erp', kind] })
    } catch (e: any) {
      setToast(`Sync failed: ${e.message}`)
    } finally {
      setSyncing(null)
      setTimeout(() => setToast(null), 6000)
    }
  }

  return (
    <div className="mb-4 space-y-3">
      <div className="flex items-center gap-2 text-xs text-neutral-500">
        {s ? <>
          Last synced: {s.last_synced_at ? new Date(s.last_synced_at).toLocaleString() : '—'}
          {' · '}<span className={cn(s.last_status === 'success' ? 'text-green-700' : s.last_status === 'failed' ? 'text-red-700' : '')}>{s.last_status || 'never'}</span>
          {' · '}{s.last_row_count} rows
        </> : 'Never synced'}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => trigger(false)} disabled={!!syncing}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-60 transition-colors">
          {syncing === 'inc' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          Sync Incremental
        </button>
        <button onClick={() => trigger(true)} disabled={!!syncing}
          className="flex items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-60 transition-colors">
          {syncing === 'full' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          Full Resync
        </button>
        <div className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 ml-auto flex-1 min-w-[180px] max-w-xs">
          <Search className="h-4 w-4 text-neutral-400 shrink-0" />
          <input className="flex-1 text-sm focus:outline-none" placeholder="Search…"
            value={search} onChange={(e) => onSearchChange(e.target.value)} />
        </div>
        {filters}
      </div>
      {toast && <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{toast}</div>}
    </div>
  )
}
```

- [ ] **Step 2: Smoke-build**

Run: `cd c:/Project/uniops/portal && npm run build`
Expected: build error — `ErpMaterialsTab`, `ErpSuppliersTab`, `ErpPersonsTab` undefined. That's expected; we add them next.

- [ ] **Step 3: Commit**

```bash
git add portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(portal): AdminPanel ERP MDM section shell + toolbar (WIP)"
```

---

### Task C3: ErpMaterialsTab

**Files:**
- Modify: `portal/src/pages/admin/AdminPanel.tsx`

- [ ] **Step 1: Append the tab component**

```tsx
interface ErpMaterial {
  erp_part_no: string; description: string | null; unit_meas: string | null
  dim_quality: string | null; part_status: string | null; item_mes_type: string | null
  erp_rowversion: string | null; synced_at: string
}

function ErpMaterialsTab() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<'A' | 'B' | ''>('A')
  const [page, setPage] = useState(1)
  const qs = new URLSearchParams({ page: String(page), page_size: '20' })
  if (search) qs.set('search', search)
  if (statusFilter) qs.set('part_status', statusFilter)
  const { data, isLoading } = useQuery<{ items: ErpMaterial[]; total: number }>({
    queryKey: ['erp', 'material', page, search, statusFilter],
    queryFn: () => mdmApi.get(`/erp/materials?${qs}`),
    placeholderData: (prev) => prev,
  })
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / 20))

  return (
    <>
      <ErpSyncToolbar
        kind="material"
        search={search}
        onSearchChange={(v) => { setSearch(v); setPage(1) }}
        filters={
          <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value as any); setPage(1) }}
            className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm focus:outline-none">
            <option value="A">Active (A)</option>
            <option value="B">Inactive (B)</option>
            <option value="">All</option>
          </select>
        }
      />
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading && !data ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
        ) : !data?.items.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">No materials. Click Sync Incremental to fetch from ERP.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Part No', 'Description', 'Unit', 'Spec', 'MES Type', 'Status', 'ERP rowversion'].map(h =>
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                )}
              </tr>
            </thead>
            <tbody>
              {data.items.map(m => (
                <tr key={m.erp_part_no} className="border-b border-neutral-100">
                  <td className="px-4 py-3 font-mono text-xs">{m.erp_part_no}</td>
                  <td className="px-4 py-3 text-neutral-800">{m.description}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{m.unit_meas}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{m.dim_quality}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{m.item_mes_type}</td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium',
                      m.part_status === 'A' ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                      {m.part_status || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-[11px] text-neutral-400">
                    {m.erp_rowversion ? new Date(m.erp_rowversion).toLocaleString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {/* Reuse existing pagination footer pattern */}
        {total > 0 && (
          <div className="flex items-center justify-between border-t border-neutral-100 px-4 py-3">
            <span className="text-xs text-neutral-400">
              {(page - 1) * 20 + 1}–{Math.min(page * 20, total)} of {total}
            </span>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="px-2 text-xs text-neutral-500">{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(portal): ERP Materials tab"
```

---

### Task C4: ErpSuppliersTab + ErpPersonsTab

**Files:**
- Modify: `portal/src/pages/admin/AdminPanel.tsx`

- [ ] **Step 1: Append the tab components**

```tsx
interface ErpSupplier {
  erp_supplier_code: string; supplier_name: string; supplier_tel: string | null
  supplier_address: string | null; supplier_type: string | null
  erp_rowversion: string | null; synced_at: string
}

function ErpSuppliersTab() {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const qs = new URLSearchParams({ page: String(page), page_size: '20' })
  if (search) qs.set('search', search)
  const { data, isLoading } = useQuery<{ items: ErpSupplier[]; total: number }>({
    queryKey: ['erp', 'supplier', page, search],
    queryFn: () => mdmApi.get(`/erp/suppliers?${qs}`),
    placeholderData: (prev) => prev,
  })
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / 20))

  return (
    <>
      <ErpSyncToolbar kind="supplier" search={search} onSearchChange={(v) => { setSearch(v); setPage(1) }} />
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading && !data ? <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
          : !data?.items.length ? <div className="py-10 text-center text-sm text-neutral-400">No suppliers. Click Sync.</div>
          : <table className="w-full text-sm">
              <thead className="border-b border-neutral-100 bg-neutral-50">
                <tr>{['Code', 'Name', 'Type', 'Tel', 'Address'].map(h =>
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.items.map(s => (
                  <tr key={s.erp_supplier_code} className="border-b border-neutral-100">
                    <td className="px-4 py-3 font-mono text-xs">{s.erp_supplier_code}</td>
                    <td className="px-4 py-3">{s.supplier_name}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{s.supplier_type}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{s.supplier_tel}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{s.supplier_address}</td>
                  </tr>
                ))}
              </tbody>
            </table>
        }
        {total > 0 && (
          <div className="flex items-center justify-between border-t border-neutral-100 px-4 py-3">
            <span className="text-xs text-neutral-400">{(page - 1) * 20 + 1}–{Math.min(page * 20, total)} of {total}</span>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="px-2 text-xs text-neutral-500">{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}

interface ErpPerson {
  erp_person_code: string; person_name: string; company_name: string | null
  department_code: string | null; department_name: string | null; is_valid: boolean
  erp_rowversion: string | null; synced_at: string
}

function ErpPersonsTab() {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const qs = new URLSearchParams({ page: String(page), page_size: '20' })
  if (search) qs.set('search', search)
  const { data, isLoading } = useQuery<{ items: ErpPerson[]; total: number }>({
    queryKey: ['erp', 'person', page, search],
    queryFn: () => mdmApi.get(`/erp/persons?${qs}`),
    placeholderData: (prev) => prev,
  })
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / 20))

  return (
    <>
      <ErpSyncToolbar kind="person" search={search} onSearchChange={(v) => { setSearch(v); setPage(1) }} />
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading && !data ? <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
          : !data?.items.length ? <div className="py-10 text-center text-sm text-neutral-400">No persons. Click Sync.</div>
          : <table className="w-full text-sm">
              <thead className="border-b border-neutral-100 bg-neutral-50">
                <tr>{['Code', 'Name', 'Company', 'Department', 'Valid'].map(h =>
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.items.map(p => (
                  <tr key={p.erp_person_code} className="border-b border-neutral-100">
                    <td className="px-4 py-3 font-mono text-xs">{p.erp_person_code}</td>
                    <td className="px-4 py-3">{p.person_name}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{p.company_name}</td>
                    <td className="px-4 py-3 text-xs text-neutral-500">{p.department_name} ({p.department_code})</td>
                    <td className="px-4 py-3">
                      <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium',
                        p.is_valid ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                        {p.is_valid ? 'Yes' : 'No'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
        }
        {total > 0 && (
          <div className="flex items-center justify-between border-t border-neutral-100 px-4 py-3">
            <span className="text-xs text-neutral-400">{(page - 1) * 20 + 1}–{Math.min(page * 20, total)} of {total}</span>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="px-2 text-xs text-neutral-500">{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}
                className="flex h-7 w-7 items-center justify-center rounded border border-neutral-200 text-neutral-500 hover:bg-neutral-50 disabled:opacity-40">
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
```

- [ ] **Step 2: Build verifies**

Run: `cd c:/Project/uniops/portal && npm run build`
Expected: success.

- [ ] **Step 3: Commit**

```bash
git add portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(portal): ERP Suppliers + Persons tabs"
```

---

### Task C5: "From ERP" import drawer in UserManagement

**Files:**
- Modify: `portal/src/pages/admin/AdminPanel.tsx`

- [ ] **Step 1: Add the From-ERP button**

Find the user management toolbar (look for the comment `{/* Add */}` near the "+ Add User" button). Insert before "+ Add User":

```tsx
<button onClick={() => setShowErpImport(true)}
  className="flex items-center gap-1.5 rounded-lg border border-[#085E5E] bg-white px-3 py-2 text-sm font-medium text-[#085E5E] hover:bg-[#085E5E]/5 transition-colors">
  <Database className="h-4 w-4" />From ERP
</button>
```

In `UserManagement()`, declare `const [showErpImport, setShowErpImport] = useState(false)`.

Below the import-result block (or anywhere inside the return), add:
```tsx
{showErpImport && <ErpUserImportDrawer onClose={() => setShowErpImport(false)} />}
```

- [ ] **Step 2: Add the drawer component**

Append above `SECTIONS`:

```tsx
function ErpUserImportDrawer({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [excludeImported, setExcludeImported] = useState(true)
  const [selected, setSelected] = useState<Record<string, { email: string }>>({})

  // Fetch the list of already-imported person codes (from epms-api users endpoint) to exclude
  const { data: existingUsers } = useQuery<{ items: ApiUser[]; total: number }>({
    queryKey: ['portal-users', 'all-for-erp-import'],
    queryFn: () => epmsApi.get('/users?page=1&page_size=1000'),
    enabled: excludeImported,
  })
  const importedCodes = (existingUsers?.items || []).map((u: any) => u.erp_person_code).filter(Boolean) as string[]

  const qs = new URLSearchParams({ page: '1', page_size: '50' })
  if (search) qs.set('search', search)
  if (excludeImported && importedCodes.length) qs.set('exclude_codes', importedCodes.join(','))

  const { data, isLoading } = useQuery<{ items: ErpPerson[]; total: number }>({
    queryKey: ['erp-import-persons', search, excludeImported, importedCodes.join(',')],
    queryFn: () => mdmApi.get(`/erp/persons?${qs}`),
  })

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ created: any[]; errors: any[] } | null>(null)

  const selectedCount = Object.keys(selected).length
  const allEmailsFilled = Object.values(selected).every(s => s.email.trim().length > 0)

  const toggle = (code: string) => {
    setSelected(prev => {
      const next = { ...prev }
      if (next[code]) delete next[code]
      else next[code] = { email: '' }
      return next
    })
  }

  const submit = async () => {
    setSubmitting(true); setError('')
    try {
      const items = Object.entries(selected).map(([code, v]) => ({
        erp_person_code: code,
        email: v.email.trim(),
        role: 'requester',
      }))
      const resp = await epmsApi.post<{ created: any[]; errors: any[] }>('/users/import-from-erp', { items })
      setResult(resp)
      qc.invalidateQueries({ queryKey: ['portal-users'] })
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex justify-end">
      <div className="bg-white w-full max-w-3xl h-full overflow-hidden flex flex-col">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <h2 className="text-sm font-semibold">Import users from ERP</h2>
          <button onClick={onClose}><X className="h-4 w-4 text-neutral-500" /></button>
        </div>

        {result ? (
          <div className="flex-1 overflow-auto p-4 space-y-3">
            <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm">
              <strong>{result.created.length} created</strong>, {result.errors.length} errors.
            </div>
            {result.created.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-neutral-600 mb-1">Temporary passwords (copy now — not shown again):</p>
                <table className="w-full text-xs border border-neutral-200">
                  <thead><tr className="bg-neutral-50">
                    <th className="px-2 py-1 text-left">Email</th>
                    <th className="px-2 py-1 text-left">Temp password</th>
                  </tr></thead>
                  <tbody>
                    {result.created.map((c, i) => (
                      <tr key={i} className="border-t border-neutral-100">
                        <td className="px-2 py-1 font-mono">{c.email}</td>
                        <td className="px-2 py-1 font-mono">{c.temp_password}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {result.errors.length > 0 && (
              <ul className="text-xs text-amber-700 list-disc pl-5">
                {result.errors.map((e, i) => <li key={i}>{e.erp_person_code}: {e.reason}</li>)}
              </ul>
            )}
            <button onClick={onClose} className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white">Done</button>
          </div>
        ) : (
          <>
            <div className="border-b border-neutral-200 px-4 py-3 space-y-2">
              <div className="flex items-center gap-2">
                <input className="flex-1 rounded-lg border border-neutral-200 px-3 py-2 text-sm"
                  placeholder="Search code or name…" value={search} onChange={(e) => setSearch(e.target.value)} />
                <label className="flex items-center gap-1 text-xs text-neutral-600">
                  <input type="checkbox" checked={excludeImported} onChange={e => setExcludeImported(e.target.checked)} />
                  Only show not-imported
                </label>
              </div>
              <p className="text-xs text-neutral-500">Role defaults to <code>requester</code>. Department auto-maps from ERP department code. Edit later in User Management.</p>
            </div>

            <div className="flex-1 overflow-auto p-4">
              {isLoading ? <div className="text-sm text-neutral-400">Loading…</div>
                : !data?.items.length ? <div className="text-sm text-neutral-400">No persons to import.</div>
                : <table className="w-full text-sm">
                    <thead className="border-b border-neutral-200">
                      <tr>
                        <th className="px-2 py-2"></th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Code</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Name</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Dept</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Email *</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map(p => {
                        const isSel = !!selected[p.erp_person_code]
                        return (
                          <tr key={p.erp_person_code} className="border-b border-neutral-100">
                            <td className="px-2 py-2"><input type="checkbox" checked={isSel} onChange={() => toggle(p.erp_person_code)} /></td>
                            <td className="px-2 py-2 font-mono text-xs">{p.erp_person_code}</td>
                            <td className="px-2 py-2">{p.person_name}</td>
                            <td className="px-2 py-2 text-xs text-neutral-500">{p.department_name}</td>
                            <td className="px-2 py-2">
                              <input
                                disabled={!isSel}
                                placeholder="email@royalmilk.com"
                                value={selected[p.erp_person_code]?.email || ''}
                                onChange={e => setSelected(prev => ({ ...prev, [p.erp_person_code]: { email: e.target.value } }))}
                                className="w-full rounded border border-neutral-200 px-2 py-1 text-xs disabled:bg-neutral-50"
                              />
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
              }
            </div>

            <div className="border-t border-neutral-200 px-4 py-3 flex items-center justify-between">
              <span className="text-xs text-neutral-500">
                {selectedCount} selected{selectedCount > 0 && !allEmailsFilled && ' · email required for each row'}
              </span>
              <div className="flex items-center gap-2">
                {error && <span className="text-xs text-red-600">{error}</span>}
                <button onClick={onClose} className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm">Cancel</button>
                <button onClick={submit} disabled={!selectedCount || !allEmailsFilled || submitting}
                  className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white disabled:opacity-60 flex items-center gap-1.5">
                  {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  Import {selectedCount} user{selectedCount === 1 ? '' : 's'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Build + dev smoke**

Run: `cd c:/Project/uniops/portal && npm run build`
Expected: success.

Run: `cd c:/Project/uniops/portal && npm run dev` and manually open AdminPanel → ERP MDM → Persons. Click Sync Incremental (with mdm-api running). Then User Management → "From ERP", verify drawer loads.

- [ ] **Step 4: Commit**

```bash
git add portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(portal): UserManagement From-ERP import drawer"
```

---

## Phase D — EPMS frontend: Vendor import + PR Type 1 picker

### Task D1: Add `mdmApi` client to EPMS

**Files:**
- Modify: `epms/src/lib/api.ts` (or wherever the API client lives)

- [ ] **Step 1: Inspect existing EPMS API client**

Run: `grep -rn "export const epmsApi\|VITE_EPMS_API\|fetch.*api/v1" c:/Project/uniops/epms/src/lib/ c:/Project/uniops/epms/src/services/ | head -10`
Expected: identifies the file pattern.

- [ ] **Step 2: Mirror the Portal pattern**

Add to `epms/src/lib/api.ts` (or equivalent — name the file the same as Portal's if it's already established):

```ts
const MDM_API = (import.meta.env.VITE_MDM_API_URL as string | undefined) || 'http://localhost:8002'

async function mdmRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  // (same shape as epmsRequest; copy fetch + auth header pattern from existing file)
  // ...
}

export const mdmApi = {
  get:    <T>(path: string)                  => mdmRequest<T>('GET', path),
  post:   <T>(path: string, body?: unknown)  => mdmRequest<T>('POST', path, body),
}
```

(Exact form must match the existing EPMS API client's conventions — read the file before editing.)

- [ ] **Step 3: Commit**

```bash
git add epms/src/lib/api.ts
git commit -m "feat(epms): add mdmApi client"
```

---

### Task D2: VendorsPage "From ERP" drawer

**Files:**
- Modify: `epms/src/pages/vendors/VendorsPage.tsx`

- [ ] **Step 1: Read the existing toolbar structure**

Run: `grep -n "Add Vendor\|onClick=.*setShow\|Toolbar\|FormData\|categories" c:/Project/uniops/epms/src/pages/vendors/VendorsPage.tsx | head -20`
Expected: identifies where the "+ Add Vendor" button lives.

- [ ] **Step 2: Add From-ERP button beside Add Vendor**

```tsx
<button onClick={() => setShowErpImport(true)}
  className="flex items-center gap-1.5 rounded-lg border border-[#085E5E] bg-white px-3 py-2 text-sm font-medium text-[#085E5E] hover:bg-[#085E5E]/5">
  From ERP
</button>
```

Add `const [showErpImport, setShowErpImport] = useState(false)` near the other state hooks.

- [ ] **Step 3: Add the drawer component**

Below the page component or in a new file `epms/src/pages/vendors/ErpVendorImportDrawer.tsx`:

```tsx
import { useState } from 'react'
import { X, Loader2 } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { mdmApi, epmsApi } from '@/lib/api'

interface ErpSupplier {
  erp_supplier_code: string; supplier_name: string; supplier_tel: string | null
  supplier_address: string | null
}

export function ErpVendorImportDrawer({
  vendorCategories, onClose,
}: { vendorCategories: string[]; onClose: () => void }) {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [excludeImported, setExcludeImported] = useState(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [category, setCategory] = useState(vendorCategories[0] || 'other')
  const [paymentTerms, setPaymentTerms] = useState('net30')
  const [currency, setCurrency] = useState('CAD')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<{ created: number; errors: any[] } | null>(null)
  const [error, setError] = useState('')

  const { data: existingVendors } = useQuery<{ items: any[]; total: number }>({
    queryKey: ['vendors-for-erp-import'],
    queryFn: () => epmsApi.get('/vendors?page=1&page_size=1000'),
    enabled: excludeImported,
  })
  const importedCodes = (existingVendors?.items || []).map((v: any) => v.erp_id).filter(Boolean) as string[]

  const qs = new URLSearchParams({ page: '1', page_size: '50' })
  if (search) qs.set('search', search)
  if (excludeImported && importedCodes.length) qs.set('exclude_codes', importedCodes.join(','))

  const { data, isLoading } = useQuery<{ items: ErpSupplier[]; total: number }>({
    queryKey: ['erp-import-suppliers', search, excludeImported, importedCodes.join(',')],
    queryFn: () => mdmApi.get(`/erp/suppliers?${qs}`),
  })

  const toggle = (code: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code); else next.add(code)
      return next
    })
  }

  const submit = async () => {
    setSubmitting(true); setError('')
    try {
      const resp = await epmsApi.post<{ created: number; errors: any[] }>('/vendors/import-from-erp', {
        erp_supplier_codes: Array.from(selected),
        defaults: { category, payment_terms: paymentTerms, currency },
      })
      setResult(resp)
      qc.invalidateQueries({ queryKey: ['vendors'] })
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex justify-end">
      <div className="bg-white w-full max-w-3xl h-full overflow-hidden flex flex-col">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <h2 className="text-sm font-semibold">Import vendors from ERP</h2>
          <button onClick={onClose}><X className="h-4 w-4 text-neutral-500" /></button>
        </div>

        {result ? (
          <div className="flex-1 overflow-auto p-4 space-y-3">
            <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm">
              <strong>{result.created} created</strong>, {result.errors.length} errors.
            </div>
            {result.errors.length > 0 && (
              <ul className="text-xs text-amber-700 list-disc pl-5">
                {result.errors.map((e, i) => <li key={i}>{e.erp_supplier_code}: {e.reason}</li>)}
              </ul>
            )}
            <p className="text-xs text-neutral-500">Vendor contact emails are placeholders (<code>code@erp.local</code>). Update them in the vendor list.</p>
            <button onClick={onClose} className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white">Done</button>
          </div>
        ) : (
          <>
            <div className="border-b border-neutral-200 px-4 py-3 space-y-2">
              <div className="grid grid-cols-3 gap-2">
                <label className="text-xs">Category
                  <select className="block w-full mt-1 rounded border border-neutral-200 px-2 py-1 text-sm"
                    value={category} onChange={e => setCategory(e.target.value)}>
                    {vendorCategories.map(c => <option key={c} value={c}>{c}</option>)}
                    {!vendorCategories.includes('other') && <option value="other">other</option>}
                  </select>
                </label>
                <label className="text-xs">Payment terms
                  <select className="block w-full mt-1 rounded border border-neutral-200 px-2 py-1 text-sm"
                    value={paymentTerms} onChange={e => setPaymentTerms(e.target.value)}>
                    {['net15','net30','net45','net60','cod','prepaid'].map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
                <label className="text-xs">Currency
                  <select className="block w-full mt-1 rounded border border-neutral-200 px-2 py-1 text-sm"
                    value={currency} onChange={e => setCurrency(e.target.value)}>
                    {['CAD','USD','EUR','CNY'].map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
              </div>
              <div className="flex items-center gap-2">
                <input className="flex-1 rounded-lg border border-neutral-200 px-3 py-2 text-sm"
                  placeholder="Search code or name…" value={search} onChange={(e) => setSearch(e.target.value)} />
                <label className="flex items-center gap-1 text-xs text-neutral-600">
                  <input type="checkbox" checked={excludeImported} onChange={e => setExcludeImported(e.target.checked)} />
                  Only not-imported
                </label>
              </div>
            </div>

            <div className="flex-1 overflow-auto p-4">
              {isLoading ? <div className="text-sm text-neutral-400">Loading…</div>
                : !data?.items.length ? <div className="text-sm text-neutral-400">No suppliers.</div>
                : <table className="w-full text-sm">
                    <thead className="border-b border-neutral-200">
                      <tr>
                        <th className="px-2 py-2"></th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Code</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Name</th>
                        <th className="px-2 py-2 text-left text-xs font-medium uppercase text-neutral-500">Tel</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map(s => (
                        <tr key={s.erp_supplier_code} className="border-b border-neutral-100">
                          <td className="px-2 py-2"><input type="checkbox" checked={selected.has(s.erp_supplier_code)} onChange={() => toggle(s.erp_supplier_code)} /></td>
                          <td className="px-2 py-2 font-mono text-xs">{s.erp_supplier_code}</td>
                          <td className="px-2 py-2">{s.supplier_name}</td>
                          <td className="px-2 py-2 text-xs text-neutral-500">{s.supplier_tel}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
              }
            </div>

            <div className="border-t border-neutral-200 px-4 py-3 flex items-center justify-between">
              <span className="text-xs text-neutral-500">{selected.size} selected · contact_email will be placeholder</span>
              <div className="flex items-center gap-2">
                {error && <span className="text-xs text-red-600">{error}</span>}
                <button onClick={onClose} className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm">Cancel</button>
                <button onClick={submit} disabled={!selected.size || submitting}
                  className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white disabled:opacity-60 flex items-center gap-1.5">
                  {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  Import {selected.size} vendor{selected.size === 1 ? '' : 's'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
```

Mount in VendorsPage:
```tsx
{showErpImport && <ErpVendorImportDrawer vendorCategories={vendorCategories} onClose={() => setShowErpImport(false)} />}
```

(`vendorCategories` already exists in VendorsPage state — confirm and reuse.)

- [ ] **Step 4: Build verifies**

Run: `cd c:/Project/uniops/epms && npm run build`
Expected: success.

- [ ] **Step 5: Commit**

```bash
git add epms/src/pages/vendors/
git commit -m "feat(epms): VendorsPage From-ERP import drawer"
```

---

### Task D3: PR Type 1 — replace parts picker with ERP materials picker

**Files:**
- Modify: `epms/src/components/pr/PrLineItems.tsx`

- [ ] **Step 1: Read existing PartsPicker structure**

Run: `grep -n "PartsPicker\|useParts\|MaterialsPicker\|isSparePartsType\|showMaterialId" c:/Project/uniops/epms/src/components/pr/PrLineItems.tsx | head -20`
Expected: identifies the existing pattern (PartsPicker only renders for type=3).

Read the existing `PartsPicker` component fully (around lines 46-200 based on earlier grep). The new `MaterialsPicker` mirrors its shape but queries mdm-api.

- [ ] **Step 2: Add MaterialsPicker component**

Append below `PartsPicker` in `PrLineItems.tsx`:

```tsx
// ─── Materials Picker (PR Type 1 — Raw materials / packaging from ERP) ───────

import { mdmApi } from '@/lib/api'

interface ErpMaterialOption {
  erp_part_no: string
  description: string | null
  unit_meas: string | null
  dim_quality: string | null
}

interface MaterialsPickerProps {
  value: string
  onSelect: (m: ErpMaterialOption) => void
}

function MaterialsPicker({ value, onSelect }: MaterialsPickerProps) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  const { data, isLoading } = useQuery<{ items: ErpMaterialOption[]; total: number }>({
    queryKey: ['erp-materials-picker', q],
    queryFn: () => mdmApi.get(`/erp/materials?part_status=A&search=${encodeURIComponent(q)}&page=1&page_size=30`),
    enabled: open,
  })

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen(true)}
        className="w-full text-left rounded border border-neutral-200 px-2 py-1 text-xs hover:bg-neutral-50">
        {value || <span className="text-neutral-400">Pick ERP material…</span>}
      </button>
      {open && (
        <div className="absolute top-full left-0 z-30 mt-1 w-[28rem] rounded-lg border border-neutral-200 bg-white shadow-lg">
          <div className="border-b border-neutral-100 p-2">
            <div className="flex items-center gap-2 rounded border border-neutral-200 px-2 py-1">
              <Search className="h-3.5 w-3.5 text-neutral-400" />
              <input autoFocus value={q} onChange={e => setQ(e.target.value)}
                placeholder="Search part no or description…"
                className="flex-1 text-xs focus:outline-none" />
            </div>
          </div>
          <div className="max-h-80 overflow-auto">
            {isLoading ? <div className="px-2 py-3 text-xs text-neutral-400">Loading…</div>
              : !data?.items.length ? <div className="px-2 py-3 text-xs text-neutral-400">No materials (try syncing in AdminPanel → ERP MDM).</div>
              : data.items.map(m => (
                <button key={m.erp_part_no} type="button"
                  onClick={() => { onSelect(m); setOpen(false); setQ('') }}
                  className="block w-full text-left px-3 py-2 hover:bg-neutral-50 border-b border-neutral-50">
                  <div className="font-mono text-xs">{m.erp_part_no}</div>
                  <div className="text-xs text-neutral-700">{m.description}</div>
                  <div className="text-[10px] text-neutral-400">{m.unit_meas} · {m.dim_quality}</div>
                </button>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Wire MaterialsPicker into the line item row for type=1**

Locate the line item rendering area in the same file (search for where `materialId` input is rendered). Adjust to:

```tsx
{procurementType === 1 ? (
  <MaterialsPicker
    value={item.materialId}
    onSelect={(m) => {
      const updated = recompute({
        ...item,
        materialId: m.erp_part_no,
        description: m.description || item.description,
        unit: m.unit_meas || item.unit,
        notes: m.dim_quality || item.notes,
      })
      handleChange(idx, updated)
    }}
  />
) : showMaterialId(procurementType) ? (
  /* existing PartsPicker for type 3, or plain input for editable case */
  <PartsPicker value={item.materialId} onSelect={...} />
) : null}
```

(The exact integration depends on the existing JSX shape — read the file, find the place where `materialId` is rendered today, and gate it on `procurementType === 1` vs `=== 3`.)

- [ ] **Step 4: Smoke build**

Run: `cd c:/Project/uniops/epms && npm run build`
Expected: success.

- [ ] **Step 5: Manual smoke**

With `mdm-api` running and at least one synced material:
1. Open EPMS, navigate to "Create PR".
2. Select procurement type 1 (Raw Materials).
3. In the line item row, the material picker should open and show ERP materials.
4. Select one — verify `materialId`, `description`, `unit`, `notes` populate.

If issues, fix and re-test.

- [ ] **Step 6: Commit**

```bash
git add epms/src/components/pr/PrLineItems.tsx
git commit -m "feat(epms): PR Type 1 line items use ERP materials picker"
```

---

## Phase E — Wiring + smoke test

### Task E1: docker-compose env wiring

**Files:**
- Modify: `docker-compose.dev.yml`

- [ ] **Step 1: Inspect compose**

Read `docker-compose.dev.yml` to find the mdm-api, epms-api, portal, epms service blocks.

- [ ] **Step 2: Add env vars**

For `mdm-api` service:
```yaml
environment:
  ...
  ERP_BASE_URL: http://10.10.95.66
  ERP_TIMEOUT_SECONDS: "60"
```

For `epms-api` service:
```yaml
environment:
  ...
  MDM_API_URL: http://mdm-api:8002
  MDM_API_TIMEOUT_SECONDS: "30"
```

For `portal` and `epms` services (or their `.env` files):
```yaml
environment:
  VITE_MDM_API_URL: http://localhost:8002
```

- [ ] **Step 3: Restart services**

Run: `cd c:/Project/uniops && docker compose -f docker-compose.dev.yml up -d --build mdm-api epms-api portal epms`
Expected: all containers come up; `./check-health.sh` returns OK for all four services.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.dev.yml
git commit -m "chore: ERP + MDM env vars in dev compose"
```

---

### Task E2: End-to-end manual smoke test

- [ ] **Step 1: Sync each kind**

1. Open Portal → AdminPanel → ERP MDM → Materials tab.
2. Click "Sync Incremental". Wait for toast.
3. Verify table populates and `last_synced_at` updates.
4. Switch to Suppliers, repeat. Switch to Persons, repeat.

If ERP is unreachable in dev, the sync should fail with a 502 toast — that's expected; document and skip the rest of the test.

- [ ] **Step 2: Import a user**

1. AdminPanel → User Management → "From ERP".
2. Search for a known person code, select 1-2 rows, fill their emails.
3. Click "Import N users". Verify success dialog shows created users + temp passwords.
4. Close dialog. Confirm new user appears in the User Management table.

- [ ] **Step 3: Import a vendor**

1. EPMS → Vendors → "From ERP".
2. Select 1-2 suppliers. Click Import.
3. Confirm new vendor rows appear with `erp_id` populated.

- [ ] **Step 4: PR Type 1 picker**

1. EPMS → New PR → procurement type 1.
2. In the first line item, click the material picker.
3. Confirm ERP materials list. Select one. Confirm line populates and is saveable.

- [ ] **Step 5: Document outcome**

Write up the smoke test result in a brief commit message and tag the PR.

```bash
git commit --allow-empty -m "test: ERP MDM integration manual smoke completed"
```

---

## Self-review notes

- **Spec coverage:** all 17 sections of the spec are mapped to tasks in Phases A–E. PR-type-1 implementation note in spec §11 is realized in Task D3.
- **Type consistency:** `erp_part_no`, `erp_supplier_code`, `erp_person_code` used consistently across models, schemas, CRUD, endpoints, and frontend types. Sync kind values are always `'material' | 'supplier' | 'person'` (singular). `material_id` field in `pr_line_items` is preserved (no rename).
- **Cross-service dependencies:** epms-api `MdmClient` forwards the caller's bearer to mdm-api; mdm-api accepts the same JWT format (both services use `jwt_secret_key` from env — if they don't share secrets, that's an ops issue that surfaces at first smoke test).
- **Known soft spots:**
  - mdm-api currently has no test harness. Task A4/A5/A6 set up `tests/`, `conftest.py`, and require a TEST_DATABASE_URL pointing at a test PG (no SQLite fallback since JSONB is used).
  - Task D3 includes a small "read existing PartsPicker first" step because the precise integration depends on the current JSX shape, which couldn't be transcribed verbatim without a full read of `PrLineItems.tsx`. The agent executing should read the file and adapt.
  - PR Type 1 currently shows `materialId` for type=1 and type=3 (`showMaterialId(t)` returns true for both). Task D3 keeps the existing PartsPicker for type=3 (unchanged) and uses the new MaterialsPicker only for type=1.
