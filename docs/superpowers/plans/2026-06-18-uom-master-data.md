# UOM Master Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make line-item Unit of Measure (UOM) configurable from the UniOps admin panel by introducing a `units_of_measure` master entity in mdm-api, consumed by EPMS PR/PO/Parts and OA Direct PA.

**Architecture:** UOM is a first-class MDM entity (mdm-api), mirroring the Department master, exposing CRUD at `/mdm/v1/uom`. Frontends read the active list through the existing `mdmApi` client and fall back to a local constant. Transaction line items keep storing the unit as a free **code string** (no cross-service FK). expense-api gains a nullable `unit` column on invoice lines so OA Direct PA can persist the choice. Conversion rates between a material's primary/secondary UOM are explicitly deferred to a future material/production module.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy (async) / Alembic / Pydantic v2 (backends); React + TypeScript + Vite + React Query + Tailwind (frontends).

**Design doc:** `docs/superpowers/specs/2026-06-18-uom-master-data-design.md`

---

## Important conventions (read before starting)

- **Unit codes keep their case.** Unlike Department codes (which are upper-cased), UOM codes are unit symbols — `kg`, `m²`, `L`, `pcs`. Do **NOT** upper-case them. Seeds use the exact current values so existing stored units keep matching.
- **No cross-service FK.** Line tables store the unit code as a plain string (same as today's `parts.unit` and EPMS PR lines). mdm-api owns the canonical list.
- **Backend tests** are DB-backed and skip unless a test DB env var is set:
  - mdm-api: `TEST_DATABASE_URL` (crud/model-level tests via `db_session`).
  - expense-api: API-level tests via the `admin_client` httpx fixture.
  - Per project convention, point these at the local docker Postgres when running locally.
- **Frontend has no unit-test runner.** Verify with the TS 6.0 typecheck command (build/`tsc -b` fails on the baseUrl deprecation):
  ```
  npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
  ```
- All user-facing frontend strings are **English only**.

## File Structure

**mdm-api (new UOM entity):**
- Create `mdm-api/app/models/uom.py` — `UnitOfMeasure` ORM model.
- Create `mdm-api/app/schemas/uom.py` — request/response schemas.
- Create `mdm-api/app/crud/uom.py` — CRUD + reference guard.
- Create `mdm-api/app/api/v1/uom.py` — REST router.
- Modify `mdm-api/app/api/v1/__init__.py` — register router.
- Modify `mdm-api/app/main.py` — import model for metadata.
- Modify `mdm-api/tests/conftest.py` — import model for metadata.
- Create `mdm-api/alembic/versions/0005_units_of_measure.py` — table + seed.
- Create `mdm-api/tests/test_uom.py` — crud tests.

**expense-api (persist unit on invoice lines):**
- Modify `expense-api/app/models/invoice.py` — add `unit` column.
- Create `expense-api/alembic/versions/0011_invoice_line_unit.py` — add column.
- Modify `expense-api/app/schemas/invoice.py` — add `unit` to response/update.
- Modify `expense-api/app/api/v1/invoices.py` — add `unit` to create schema + loop.
- Modify `expense-api/tests/test_invoices.py` — assert unit round-trips.

**EPMS frontend:**
- Create `epms/src/services/uom.ts` — uom service over `mdmApi`.
- Create `epms/src/hooks/useUoms.ts` — React Query hook.
- Modify `epms/src/components/pr/PrLineItems.tsx` — source units from hook.
- Modify `epms/src/pages/parts/PartsListPage.tsx` — source units from hook.

**OA frontend:**
- Create `oa/src/hooks/useUoms.ts` — React Query hook over `mdmApi`.
- Modify `oa/src/pages/pa/PaDirectCreatePage.tsx` — add Unit column, payload, preview.

**Portal frontend (admin UI):**
- Create `portal/src/pages/admin/UnitsOfMeasure.tsx` — admin section component.
- Modify `portal/src/pages/admin/AdminPanel.tsx` — register section + nav entry.

---

## Phase 1 — mdm-api UOM entity

### Task 1: UOM ORM model

**Files:**
- Create: `mdm-api/app/models/uom.py`
- Modify: `mdm-api/app/main.py:8`
- Modify: `mdm-api/tests/conftest.py:8-12`

- [ ] **Step 1: Create the model**

`mdm-api/app/models/uom.py`:
```python
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class UnitOfMeasure(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "units_of_measure"

    # Unit symbols keep their case (kg, m², L, pcs) — do NOT upper-case.
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # count | mass | volume | length | area | time | other
    dimension: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
```

- [ ] **Step 2: Register the model for metadata in main.py**

In `mdm-api/app/main.py`, line 8, add `uom` to the model import list:
```python
from app.models import vendor, department, cost_center, part, user, company, erp_material, erp_supplier, erp_person, erp_sync_state, uom  # noqa: F401
```

- [ ] **Step 3: Register the model in the test conftest**

In `mdm-api/tests/conftest.py`, add `uom` to the model import block (lines 8-12):
```python
from app.models import (  # noqa: F401
    vendor, department, cost_center, part, user, company,
    erp_material, erp_supplier, erp_person, erp_sync_state, tax,
    business_partner, uom,
)
```

- [ ] **Step 4: Verify the model imports cleanly**

Run: `cd mdm-api && python -c "from app.models.uom import UnitOfMeasure; print(UnitOfMeasure.__tablename__)"`
Expected: prints `units_of_measure`

- [ ] **Step 5: Commit**

```bash
git add mdm-api/app/models/uom.py mdm-api/app/main.py mdm-api/tests/conftest.py
git commit -m "feat(mdm): add UnitOfMeasure model"
```

---

### Task 2: UOM schemas

**Files:**
- Create: `mdm-api/app/schemas/uom.py`

- [ ] **Step 1: Create the schemas**

`mdm-api/app/schemas/uom.py`:
```python
"""Pydantic schemas for Unit of Measure (mdm-api)."""
import uuid
from typing import Literal

from pydantic import BaseModel, Field

Dimension = Literal["count", "mass", "volume", "length", "area", "time", "other"]


class UomCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    dimension: Dimension = "other"
    is_active: bool = True


class UomUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    dimension: Dimension | None = None
    is_active: bool | None = None


class UomResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    dimension: str
    is_active: bool

    model_config = {"from_attributes": True}


class UomListResponse(BaseModel):
    items: list[UomResponse]
    total: int
```

- [ ] **Step 2: Verify the schemas import cleanly**

Run: `cd mdm-api && python -c "from app.schemas.uom import UomCreate; print(UomCreate(code='kg', name='Kilogram', dimension='mass'))"`
Expected: prints a `UomCreate` instance with `code='kg'`

- [ ] **Step 3: Commit**

```bash
git add mdm-api/app/schemas/uom.py
git commit -m "feat(mdm): add UOM schemas"
```

---

### Task 3: UOM CRUD (with tests)

**Files:**
- Create: `mdm-api/app/crud/uom.py`
- Test: `mdm-api/tests/test_uom.py`

- [ ] **Step 1: Write the failing tests**

`mdm-api/tests/test_uom.py`:
```python
"""UnitOfMeasure CRUD — code case preserved, dedup, reference guard."""
import pytest

from app.crud import uom as uom_crud
from app.models.part import Part
from app.schemas.uom import UomCreate, UomUpdate


async def test_create_preserves_case_and_reads_back(db_session):
    created = await uom_crud.create(db_session, UomCreate(code="kg", name="Kilogram", dimension="mass"))
    assert created.code == "kg"  # NOT upper-cased
    assert created.dimension == "mass"

    fetched = await uom_crud.get_by_code(db_session, "kg")
    assert fetched is not None
    assert fetched.id == created.id


async def test_get_all_active_only(db_session):
    await uom_crud.create(db_session, UomCreate(code="pcs", name="Pieces", dimension="count"))
    inactive = await uom_crud.create(db_session, UomCreate(code="box", name="Box", dimension="count"))
    await uom_crud.update(db_session, inactive, UomUpdate(is_active=False))

    codes_all = {u.code for u in await uom_crud.get_all(db_session, active_only=False)}
    codes_active = {u.code for u in await uom_crud.get_all(db_session, active_only=True)}
    assert {"pcs", "box"} <= codes_all
    assert "pcs" in codes_active
    assert "box" not in codes_active


async def test_update_changes_fields(db_session):
    u = await uom_crud.create(db_session, UomCreate(code="m", name="Meter", dimension="length"))
    updated = await uom_crud.update(db_session, u, UomUpdate(name="Metre", is_active=False))
    assert updated.name == "Metre"
    assert updated.is_active is False


async def test_count_references_counts_parts_using_code(db_session):
    await uom_crud.create(db_session, UomCreate(code="roll", name="Roll", dimension="count"))
    db_session.add(Part(
        code="P-UOM-1", category="Misc", name="Tape", description=None,
        supplier="ACME", supplier_part_no="SP1", unit_price=0, unit="roll", is_active=True,
    ))
    await db_session.flush()
    refs = await uom_crud.count_references(db_session, "roll")
    assert refs["parts"] == 1
    assert (await uom_crud.count_references(db_session, "lot"))["parts"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mdm-api && TEST_DATABASE_URL=$MDM_TEST_DATABASE_URL pytest tests/test_uom.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.crud.uom'`
(If `TEST_DATABASE_URL` is unset the tests will SKIP — set it to the local docker test DB first.)

- [ ] **Step 3: Write the CRUD implementation**

`mdm-api/app/crud/uom.py`:
```python
"""CRUD operations for Unit of Measure (mdm-api owns writes)."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.part import Part
from app.models.uom import UnitOfMeasure
from app.schemas.uom import UomCreate, UomUpdate


async def get_all(db: AsyncSession, *, active_only: bool = False) -> list[UnitOfMeasure]:
    q = select(UnitOfMeasure)
    if active_only:
        q = q.where(UnitOfMeasure.is_active.is_(True))
    return list((await db.execute(q.order_by(UnitOfMeasure.code))).scalars().all())


async def get_by_id(db: AsyncSession, uom_id: uuid.UUID) -> UnitOfMeasure | None:
    return (await db.execute(
        select(UnitOfMeasure).where(UnitOfMeasure.id == uom_id)
    )).scalar_one_or_none()


async def get_by_code(db: AsyncSession, code: str) -> UnitOfMeasure | None:
    # Case-sensitive: unit symbols keep their case (kg, m², L).
    return (await db.execute(
        select(UnitOfMeasure).where(UnitOfMeasure.code == code)
    )).scalar_one_or_none()


async def create(db: AsyncSession, payload: UomCreate) -> UnitOfMeasure:
    u = UnitOfMeasure(
        code=payload.code, name=payload.name,
        dimension=payload.dimension, is_active=payload.is_active,
    )
    db.add(u)
    await db.flush()
    await db.refresh(u)
    return u


async def update(db: AsyncSession, u: UnitOfMeasure, payload: UomUpdate) -> UnitOfMeasure:
    if payload.code is not None:
        u.code = payload.code
    if payload.name is not None:
        u.name = payload.name
    if payload.dimension is not None:
        u.dimension = payload.dimension
    if payload.is_active is not None:
        u.is_active = payload.is_active
    await db.flush()
    await db.refresh(u)
    return u


async def count_references(db: AsyncSession, code: str) -> dict:
    part_count = (await db.execute(
        select(func.count()).where(Part.unit == code)
    )).scalar_one()
    return {"parts": part_count}


async def delete(db: AsyncSession, u: UnitOfMeasure) -> None:
    await db.delete(u)
    await db.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mdm-api && TEST_DATABASE_URL=$MDM_TEST_DATABASE_URL pytest tests/test_uom.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mdm-api/app/crud/uom.py mdm-api/tests/test_uom.py
git commit -m "feat(mdm): add UOM CRUD with reference guard + tests"
```

---

### Task 4: UOM REST router

**Files:**
- Create: `mdm-api/app/api/v1/uom.py`
- Modify: `mdm-api/app/api/v1/__init__.py`

- [ ] **Step 1: Create the router (mirrors departments.py)**

`mdm-api/app/api/v1/uom.py`:
```python
"""Unit of Measure endpoints (mdm-api owns writes).

Reads: any authenticated role. Writes: system_admin | finance_manager | ap_clerk.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_roles
from app.crud import uom as uom_crud
from app.db.base import get_db
from app.schemas.uom import UomCreate, UomListResponse, UomResponse, UomUpdate

router = APIRouter(prefix="/uom", tags=["uom"])

WriteDep = Annotated[dict, Depends(require_roles("system_admin", "finance_manager", "ap_clerk"))]


@router.get("", response_model=UomListResponse)
async def list_uoms(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    active_only: bool = Query(default=False),
):
    items = await uom_crud.get_all(db, active_only=active_only)
    return UomListResponse(items=items, total=len(items))


@router.post("", response_model=UomResponse, status_code=status.HTTP_201_CREATED)
async def create_uom(
    body: UomCreate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    if await uom_crud.get_by_code(db, body.code):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already exists")
    return await uom_crud.create(db, body)


@router.get("/by-code/{code}", response_model=UomResponse)
async def get_uom_by_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    u = await uom_crud.get_by_code(db, code)
    if not u:
        raise HTTPException(status_code=404, detail="Unit of measure not found")
    return u


@router.get("/{uom_id}", response_model=UomResponse)
async def get_uom(
    uom_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    u = await uom_crud.get_by_id(db, uom_id)
    if not u:
        raise HTTPException(status_code=404, detail="Unit of measure not found")
    return u


@router.patch("/{uom_id}", response_model=UomResponse)
async def update_uom(
    uom_id: uuid.UUID,
    body: UomUpdate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    u = await uom_crud.get_by_id(db, uom_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Unit of measure not found")
    return await uom_crud.update(db, u, body)


@router.delete("/{uom_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_uom(
    uom_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    u = await uom_crud.get_by_id(db, uom_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Unit of measure not found")
    refs = await uom_crud.count_references(db, u.code)
    if refs["parts"] > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Unit is used by {refs['parts']} part(s) and cannot be deleted. Deactivate it instead.",
        )
    await uom_crud.delete(db, u)
```

- [ ] **Step 2: Register the router**

In `mdm-api/app/api/v1/__init__.py`, add the import and `include_router` call following the existing pattern:
```python
from app.api.v1.uom import router as uom_router
```
and after `api_router.include_router(partners_router)`:
```python
api_router.include_router(uom_router)
```

- [ ] **Step 3: Verify the app boots with the new route**

Run: `cd mdm-api && python -c "from app.main import app; print([r.path for r in app.routes if '/uom' in r.path])"`
Expected: a list including `/mdm/v1/uom`, `/mdm/v1/uom/{uom_id}`, `/mdm/v1/uom/by-code/{code}`

- [ ] **Step 4: Commit**

```bash
git add mdm-api/app/api/v1/uom.py mdm-api/app/api/v1/__init__.py
git commit -m "feat(mdm): add UOM REST router"
```

---

### Task 5: Alembic migration + seed

**Files:**
- Create: `mdm-api/alembic/versions/0005_units_of_measure.py`

- [ ] **Step 1: Create the migration**

`mdm-api/alembic/versions/0005_units_of_measure.py`:
```python
"""units_of_measure master + seed of current built-in units

Unit symbols keep their case (kg, m², L, pcs) so existing stored line-item
units keep matching. Conversion rates (primary/secondary UOM per material)
are deferred to the future material/production module.

Revision ID: 0005_units_of_measure
Revises: 0004_business_partners
Create Date: 2026-06-18
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_units_of_measure"
down_revision = "0004_business_partners"
branch_labels = None
depends_on = None


def upgrade():
    uom = op.create_table(
        "units_of_measure",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False, server_default="other"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_units_of_measure_code"),
    )
    op.create_index("ix_units_of_measure_code", "units_of_measure", ["code"], unique=True)

    def row(code, name, dim):
        return dict(id=uuid.uuid4(), code=code, name=name, dimension=dim, is_active=True)

    op.bulk_insert(uom, [
        row("pcs", "Pieces", "count"),
        row("kg", "Kilogram", "mass"),
        row("set", "Set", "count"),
        row("pair", "Pair", "count"),
        row("box", "Box", "count"),
        row("carton", "Carton", "count"),
        row("roll", "Roll", "count"),
        row("m", "Meter", "length"),
        row("m²", "Square Meter", "area"),
        row("L", "Liter", "volume"),
        row("hour", "Hour", "time"),
        row("month", "Month", "time"),
        row("lot", "Lot", "count"),
    ])


def downgrade():
    op.drop_index("ix_units_of_measure_code", table_name="units_of_measure")
    op.drop_table("units_of_measure")
```

- [ ] **Step 2: Apply the migration against the dev DB**

Run: `cd mdm-api && alembic upgrade head`
Expected: `Running upgrade 0004_business_partners -> 0005_units_of_measure`

- [ ] **Step 3: Verify the seed**

Run: `psql "$DATABASE_URL" -c "select code, dimension from units_of_measure order by code;"`
Expected: 13 rows; codes match the seed exactly, including `m²` (area) and `L` (volume) with their exact case.
(If `psql` is not available, query via any DB client or a short async script — the check is: exactly 13 rows and codes match the seed case-for-case.)

- [ ] **Step 4: Commit**

```bash
git add mdm-api/alembic/versions/0005_units_of_measure.py
git commit -m "feat(mdm): migration + seed for units_of_measure"
```

---

## Phase 2 — expense-api: persist unit on invoice lines

### Task 6: Add `unit` column to invoice lines

**Files:**
- Modify: `expense-api/app/models/invoice.py:70`
- Create: `expense-api/alembic/versions/0011_invoice_line_unit.py`
- Modify: `expense-api/app/schemas/invoice.py:10-32`
- Modify: `expense-api/app/api/v1/invoices.py:21-28,139-148`
- Test: `expense-api/tests/test_invoices.py`

- [ ] **Step 1: Write the failing test**

In `expense-api/tests/test_invoices.py`, add at the end of the file:
```python
@pytest.mark.asyncio
async def test_invoice_line_unit_roundtrip(admin_client):
    payload = _invoice_payload()
    payload["lines"][0]["unit"] = "kg"
    resp = await admin_client.post("/api/v1/invoices", json=payload)
    assert resp.status_code == 201
    assert resp.json()["lines"][0]["unit"] == "kg"


@pytest.mark.asyncio
async def test_invoice_line_unit_optional(admin_client):
    """Lines without a unit still serialize (unit is nullable)."""
    resp = await admin_client.post("/api/v1/invoices", json=_invoice_payload())
    assert resp.status_code == 201
    assert resp.json()["lines"][0]["unit"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd expense-api && pytest tests/test_invoices.py::test_invoice_line_unit_roundtrip tests/test_invoices.py::test_invoice_line_unit_optional -v`
Expected: FAIL — `test_invoice_line_unit_roundtrip` returns `unit` missing/None (KeyError or assertion), because the field is not yet persisted/serialized.

- [ ] **Step 3: Add the model column**

In `expense-api/app/models/invoice.py`, in `ExpenseInvoiceLine` after the `tax_amount` column (line 70), add:
```python
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
```

- [ ] **Step 4: Create the migration**

`expense-api/alembic/versions/0011_invoice_line_unit.py`:
```python
"""expense_invoice_lines.unit — UOM code per line (OA Direct PA)

Free string referencing mdm-api units_of_measure by code; no FK by design.

Revision ID: 0011_invoice_line_unit
Revises: 0010_line_tax_code
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_invoice_line_unit"
down_revision = "0010_line_tax_code"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("expense_invoice_lines", sa.Column("unit", sa.String(50), nullable=True))


def downgrade():
    op.drop_column("expense_invoice_lines", "unit")
```

- [ ] **Step 5: Add `unit` to the schemas**

In `expense-api/app/schemas/invoice.py`, add `unit: Optional[str]` to `InvoiceLineResponse` (after `tax_amount`, line 18):
```python
    unit: Optional[str] = None
```
and to `InvoiceLineUpdate` (after `tax_amount`, line 29):
```python
    unit: Optional[str] = None
```

- [ ] **Step 6: Add `unit` to the create schema + persistence loop**

In `expense-api/app/api/v1/invoices.py`, add this field to `InvoiceLineCreate` (after its `tax_amount` field, around line 27):
```python
    unit: str | None = None
```
Then in the create loop (around lines 139-148) where `ExpenseInvoiceLine(...)` is built, add the `unit` kwarg:
```python
    for li in body.lines:
        db.add(ExpenseInvoiceLine(
            invoice_id=inv.id,
            line_number=li.line_number,
            description=li.description,
            quantity=li.quantity,
            unit_price=li.unit_price,
            amount=li.amount,
            tax_amount=li.tax_amount,
            unit=li.unit,
        ))
```
(Match the existing kwargs already present in the loop; only the `unit=li.unit` line is new.)

- [ ] **Step 7: Apply the migration**

Run: `cd expense-api && alembic upgrade head`
Expected: `Running upgrade 0010_line_tax_code -> 0011_invoice_line_unit`

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd expense-api && pytest tests/test_invoices.py -v`
Expected: PASS (including the two new tests)

- [ ] **Step 9: Commit**

```bash
git add expense-api/app/models/invoice.py expense-api/alembic/versions/0011_invoice_line_unit.py expense-api/app/schemas/invoice.py expense-api/app/api/v1/invoices.py expense-api/tests/test_invoices.py
git commit -m "feat(expense): persist unit on invoice lines"
```

---

## Phase 3 — EPMS frontend

### Task 7: UOM service + hook (EPMS)

**Files:**
- Create: `epms/src/services/uom.ts`
- Create: `epms/src/hooks/useUoms.ts`

- [ ] **Step 1: Create the service**

`epms/src/services/uom.ts`:
```typescript
import { mdmApi } from '@/lib/api'

export type UomDimension = 'count' | 'mass' | 'volume' | 'length' | 'area' | 'time' | 'other'

export interface ApiUom {
  id: string
  code: string
  name: string
  dimension: UomDimension
  is_active: boolean
}

export interface UomListResponse {
  items: ApiUom[]
  total: number
}

export const uomService = {
  list: (activeOnly = true) =>
    mdmApi.get<UomListResponse>('/uom', { active_only: activeOnly }),
}
```

- [ ] **Step 2: Create the hook**

`epms/src/hooks/useUoms.ts`:
```typescript
import { useQuery } from '@tanstack/react-query'
import { uomService } from '@/services/uom'
import { LINE_ITEM_UNITS } from '@/types'

/**
 * Active UOM codes for line-item dropdowns. Falls back to the built-in
 * LINE_ITEM_UNITS constant while loading or if the master list is empty.
 */
export function useUomCodes(): string[] {
  const { data } = useQuery({
    queryKey: ['uoms', 'active'],
    queryFn: () => uomService.list(true),
    staleTime: 60_000,
  })
  const codes = data?.items.map((u) => u.code) ?? []
  return codes.length > 0 ? codes : [...LINE_ITEM_UNITS]
}
```

- [ ] **Step 3: Typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add epms/src/services/uom.ts epms/src/hooks/useUoms.ts
git commit -m "feat(epms): UOM service + useUomCodes hook"
```

---

### Task 8: PR line editor reads UOM from master

**Files:**
- Modify: `epms/src/components/pr/PrLineItems.tsx:7,528-530,705-707`

- [ ] **Step 1: Import the hook and resolve the unit list**

In `epms/src/components/pr/PrLineItems.tsx`:
- Keep the existing `import { LINE_ITEM_UNITS } from '@/types'` (still used as the fallback inside the hook).
- Add: `import { useUomCodes } from '@/hooks/useUoms'`.
- Inside the `PrLineItems` component body (near the other hooks like `useParts`), add:
```typescript
  const uomCodes = useUomCodes()
```

- [ ] **Step 2: Add a helper that injects a saved-but-removed unit**

In the same component body, after `const uomCodes = useUomCodes()`, add:
```typescript
  // Ensure a line's currently-saved unit is always selectable, even if it was
  // removed from the master list after the line was created.
  const unitOptions = (current: string): string[] =>
    current && !uomCodes.includes(current) ? [current, ...uomCodes] : uomCodes
```

- [ ] **Step 3: Use the resolved options in the table dropdown**

Replace the table-row unit `<select>` options block (currently around lines 528-530):
```tsx
                      {LINE_ITEM_UNITS.map((u) => (
                        <option key={u} value={u}>{u}</option>
                      ))}
```
with:
```tsx
                      {unitOptions(item.unit).map((u) => (
                        <option key={u} value={u}>{u}</option>
                      ))}
```

- [ ] **Step 4: Use the resolved options in the mobile/card dropdown**

Replace the second unit `<select>` options block (currently around lines 705-707):
```tsx
                    {LINE_ITEM_UNITS.map((u) => (
                      <option key={u} value={u}>{u}</option>
                    ))}
```
with:
```tsx
                    {unitOptions(item.unit).map((u) => (
                      <option key={u} value={u}>{u}</option>
                    ))}
```

- [ ] **Step 5: Typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors

- [ ] **Step 6: Manual verification**

Start the app (or use the running dev server), open **New PR**, and confirm the Unit dropdown lists the units from the master (default 13). With mdm-api running, deactivate one unit in the admin panel later (Task 11) and confirm it disappears here. Confirm PO create (which reuses this component) shows the same list.

- [ ] **Step 7: Commit**

```bash
git add epms/src/components/pr/PrLineItems.tsx
git commit -m "feat(epms): PR/PO line units sourced from UOM master"
```

---

### Task 9: Parts master reads UOM from master

**Files:**
- Modify: `epms/src/pages/parts/PartsListPage.tsx:15,287-290`

- [ ] **Step 1: Import the hook**

In `epms/src/pages/parts/PartsListPage.tsx`:
- Keep `import { LINE_ITEM_UNITS } from '@/types'` (the hook uses it as fallback; if no other usage remains in this file after Step 3, remove this import to satisfy eslint).
- Add: `import { useUomCodes } from '@/hooks/useUoms'`.

- [ ] **Step 2: Resolve the unit list in the form component**

In the component that renders the Unit (UOM) `<select>` (the one holding `form.unit`), add near its other hooks:
```typescript
  const uomCodes = useUomCodes()
  const unitOptions = form.unit && !uomCodes.includes(form.unit)
    ? [form.unit, ...uomCodes]
    : uomCodes
```

- [ ] **Step 3: Use the resolved options**

Replace the Unit (UOM) options block (currently around lines 287-290):
```tsx
              {LINE_ITEM_UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
```
with:
```tsx
              {unitOptions.map((u) => <option key={u} value={u}>{u}</option>)}
```

- [ ] **Step 4: Typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors (if you removed the now-unused `LINE_ITEM_UNITS` import, confirm nothing else in the file references it)

- [ ] **Step 5: Commit**

```bash
git add epms/src/pages/parts/PartsListPage.tsx
git commit -m "feat(epms): Parts UOM dropdown sourced from UOM master"
```

---

## Phase 4 — OA frontend (Direct PA)

### Task 10: Add Unit selector to OA Direct PA lines

**Files:**
- Create: `oa/src/hooks/useUoms.ts`
- Modify: `oa/src/pages/pa/PaDirectCreatePage.tsx:538-541,553-567 (line editor grid), 691-698 (payload), 838-854 (preview)`

- [ ] **Step 1: Create the OA hook**

`oa/src/hooks/useUoms.ts`:
```typescript
import { useQuery } from '@tanstack/react-query'
import { mdmApi } from '@/lib/api'

interface ApiUom { id: string; code: string; name: string; dimension: string; is_active: boolean }
interface UomListResponse { items: ApiUom[]; total: number }

// Built-in fallback mirrors EPMS LINE_ITEM_UNITS (OA has no shared constant).
const FALLBACK_UNITS = ['pcs', 'kg', 'set', 'pair', 'box', 'carton', 'roll', 'm', 'm²', 'L', 'hour', 'month', 'lot']

export function useUomCodes(): string[] {
  const { data } = useQuery({
    queryKey: ['uoms', 'active'],
    queryFn: () => mdmApi.get<UomListResponse>('/uom?active_only=true'),
    staleTime: 60_000,
  })
  const codes = data?.items.map((u) => u.code) ?? []
  return codes.length > 0 ? codes : FALLBACK_UNITS
}
```

- [ ] **Step 2: Import the hook and resolve codes in the line editor**

In `oa/src/pages/pa/PaDirectCreatePage.tsx`, add the import near the top with the other hooks/imports:
```typescript
import { useUomCodes } from '@/hooks/useUoms'
```
In the component that renders the line-item editor grid (the one with `addLineItem`/`updateLineItem` and the `['Description', 'Qty', 'Unit Price', 'Total', '']` header), add:
```typescript
  const uomCodes = useUomCodes()
  const unitOptions = (current: string | null): string[] =>
    current && !uomCodes.includes(current) ? [current, ...uomCodes] : uomCodes
```

- [ ] **Step 3: Add a "Unit" column to the editor header**

Replace the header grid (currently around lines 538-541):
```tsx
                <div className="grid grid-cols-[1fr_48px_72px_72px_20px] gap-1 px-1">
                  {['Description', 'Qty', 'Unit Price', 'Total', ''].map(h => (
                    <span key={h} className="text-[10px] font-semibold uppercase tracking-wide text-neutral-400">{h}</span>
                  ))}
                </div>
```
with (adds a 56px Unit column between Qty and Unit Price):
```tsx
                <div className="grid grid-cols-[1fr_48px_56px_72px_72px_20px] gap-1 px-1">
                  {['Description', 'Qty', 'Unit', 'Unit Price', 'Total', ''].map(h => (
                    <span key={h} className="text-[10px] font-semibold uppercase tracking-wide text-neutral-400">{h}</span>
                  ))}
                </div>
```

- [ ] **Step 4: Add the Unit dropdown to each editor row**

In the same editor, the row container's grid class must match the header. Update the row's grid column template from `grid-cols-[1fr_48px_72px_72px_20px]` to `grid-cols-[1fr_48px_56px_72px_72px_20px]`, then insert a Unit `<select>` immediately after the Qty input (the input whose `onChange` sets `quantity`) and before the Unit Price input:
```tsx
                    <select
                      value={item.unit ?? ''}
                      onChange={e => updateLineItem(i, { unit: e.target.value || null })}
                      className="rounded border border-neutral-200 px-1 py-1 text-xs focus:outline-none focus:border-primary-400"
                    >
                      <option value="">—</option>
                      {unitOptions(item.unit).map(u => <option key={u} value={u}>{u}</option>)}
                    </select>
```
(Note: the row's grid template literal appears on the row wrapper element near the Qty/Unit Price inputs — change that one occurrence to the 6-column template above so the columns line up.)

- [ ] **Step 5: Default manually-added lines to the first configured unit**

Update `addLineItem` (currently around line 347) so new manual lines pre-select the first available unit (OCR-extracted lines remain whatever the parser set — `null`):
```tsx
  const addLineItem = () =>
    setLineItems(prev => [...prev, { description: '', quantity: 1, unit: uomCodes[0] ?? null, unit_price: 0, line_total: 0 }])
```

- [ ] **Step 6: Include `unit` in the invoice-line payload**

In the create handler, the invoice POST builds `lines` (currently around lines 691-698). Add `unit`:
```tsx
        lines: (fields.lineItems ?? []).map((li, i) => ({
          line_number: i + 1,
          description: li.description,
          quantity: li.quantity,
          unit: li.unit,
          unit_price: li.unit_price,
          amount: li.line_total,
          tax_amount: 0,
        })),
```

- [ ] **Step 7: Show the unit in the preview table**

In the preview table (the read-only line table around lines 838-854 with headers Description / Qty / Unit Price / Total), append the unit to the Qty cell so it reads e.g. `2 kg`. Replace the Qty cell:
```tsx
                  <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-600">{li.quantity}</td>
```
with:
```tsx
                  <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-600">
                    {li.quantity}{li.unit ? ` ${li.unit}` : ''}
                  </td>
```

- [ ] **Step 8: Typecheck**

Run: `cd oa && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors

- [ ] **Step 9: Manual verification**

With mdm-api + expense-api running, open **OA → New Direct PA**, upload/parse an invoice, add a manual line: confirm the Unit dropdown lists master units and defaults to the first. Submit and confirm the created invoice's lines persist the chosen `unit` (check the GET invoice response or DB). Confirm the preview shows `qty unit`.

- [ ] **Step 10: Commit**

```bash
git add oa/src/hooks/useUoms.ts oa/src/pages/pa/PaDirectCreatePage.tsx
git commit -m "feat(oa): Direct PA line Unit selector sourced from UOM master"
```

---

## Phase 5 — Portal admin UI

### Task 11: "Units of Measure" admin section

**Files:**
- Create: `portal/src/pages/admin/UnitsOfMeasure.tsx`
- Modify: `portal/src/pages/admin/AdminPanel.tsx` (nav registration — see Step 3)

- [ ] **Step 1: Build the admin section component (mirrors DepartmentManagement)**

`portal/src/pages/admin/UnitsOfMeasure.tsx`:
```tsx
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, X } from 'lucide-react'
import { mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'

type Dimension = 'count' | 'mass' | 'volume' | 'length' | 'area' | 'time' | 'other'
const DIMENSIONS: Dimension[] = ['count', 'mass', 'volume', 'length', 'area', 'time', 'other']

interface ApiUom {
  id: string
  code: string
  name: string
  dimension: Dimension
  is_active: boolean
}

// Shared UI atoms duplicated from AdminPanel are intentionally avoided; this
// component uses plain Tailwind to stay self-contained.
function SectionHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-6 border-b border-neutral-100 pb-4">
      <h2 className="text-lg font-semibold text-neutral-900">{title}</h2>
      <p className="mt-0.5 text-sm text-neutral-500">{description}</p>
    </div>
  )
}

export function UnitsOfMeasure() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery<{ items: ApiUom[]; total: number }>({
    queryKey: ['portal-uoms'],
    queryFn: () => mdmApi.get<{ items: ApiUom[]; total: number }>('/uom'),
  })
  const [modal, setModal] = useState<{ mode: 'create' | 'edit'; uom?: ApiUom } | null>(null)
  const [form, setForm] = useState<{ code: string; name: string; dimension: Dimension; is_active: boolean }>(
    { code: '', name: '', dimension: 'count', is_active: true },
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const openCreate = () => { setForm({ code: '', name: '', dimension: 'count', is_active: true }); setError(''); setModal({ mode: 'create' }) }
  const openEdit = (u: ApiUom) => { setForm({ code: u.code, name: u.name, dimension: u.dimension, is_active: u.is_active }); setError(''); setModal({ mode: 'edit', uom: u }) }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault(); setError(''); setSaving(true)
    try {
      if (modal?.mode === 'create') {
        await mdmApi.post('/uom', form)
      } else {
        await mdmApi.patch(`/uom/${modal?.uom?.id}`, form)
      }
      qc.invalidateQueries({ queryKey: ['portal-uoms'] })
      setModal(null)
    } catch (err: any) { setError(err.message) } finally { setSaving(false) }
  }

  const handleDelete = async (u: ApiUom) => {
    if (!confirm(`Delete unit "${u.code}"?`)) return
    try {
      await mdmApi.delete(`/uom/${u.id}`)
      qc.invalidateQueries({ queryKey: ['portal-uoms'] })
    } catch (err: any) { alert(err.message) }
  }

  return (
    <div>
      <SectionHeader title="Units of Measure" description="Units available in line-item pickers across EPMS (PR/PO/Parts) and OA. Shared master data." />
      <div className="mb-4 flex justify-end">
        <button onClick={openCreate}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A]">
          <Plus className="h-4 w-4" />Add Unit
        </button>
      </div>
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
        ) : !data?.items.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">No units yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Code', 'Name', 'Dimension', 'Status', ''].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((u, i) => (
                <tr key={u.id} className={cn('border-b border-neutral-100', i === data.items.length - 1 && 'border-b-0')}>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-700">{u.code}</td>
                  <td className="px-4 py-3 text-neutral-800">{u.name}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{u.dimension}</td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', u.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                      {u.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => openEdit(u)} className="rounded p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"><Pencil className="h-3.5 w-3.5" /></button>
                      <button onClick={() => handleDelete(u)} className="rounded p-1.5 text-neutral-400 hover:bg-red-50 hover:text-red-500"><Trash2 className="h-3.5 w-3.5" /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {modal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-xl border border-neutral-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-4">
              <h3 className="font-semibold text-neutral-900">{modal.mode === 'create' ? 'Add Unit' : 'Edit Unit'}</h3>
              <button onClick={() => setModal(null)} className="rounded p-1 text-neutral-400 hover:bg-neutral-100"><X className="h-4 w-4" /></button>
            </div>
            <form onSubmit={handleSave} className="flex flex-col gap-4 p-5">
              <div>
                <label className="mb-1 block text-xs font-medium text-neutral-600">Code</label>
                <input value={form.code} onChange={(e) => setForm((p) => ({ ...p, code: e.target.value }))}
                  className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
                  placeholder="kg" required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-neutral-600">Name</label>
                <input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                  className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
                  placeholder="Kilogram" required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-neutral-600">Dimension</label>
                <select value={form.dimension} onChange={(e) => setForm((p) => ({ ...p, dimension: e.target.value as Dimension }))}
                  className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400">
                  {DIMENSIONS.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-neutral-700">Active</span>
                <button type="button" onClick={() => setForm((p) => ({ ...p, is_active: !p.is_active }))}
                  className={cn('relative inline-flex h-5 w-9 items-center rounded-full transition-colors', form.is_active ? 'bg-primary-600' : 'bg-neutral-200')}>
                  <span className={cn('inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform', form.is_active ? 'translate-x-4.5' : 'translate-x-0.5')} />
                </button>
              </div>
              {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
              <div className="flex justify-end gap-2 border-t border-neutral-100 pt-3">
                <button type="button" onClick={() => setModal(null)} className="rounded-lg border border-neutral-200 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50">Cancel</button>
                <button type="submit" disabled={saving}
                  className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-60">
                  {saving ? 'Saving…' : 'Save Changes'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Locate the admin nav registration**

The nav/section registry lives in `portal/src/pages/admin/AdminPanel.tsx` below line 1083 (not yet read in this plan). Run:
```
grep -n "DepartmentManagement\|CurrencySettings\|NAV\|sections\|activeSection\|label:" portal/src/pages/admin/AdminPanel.tsx
```
Expected: a navigation array (e.g. an array of `{ key, label, icon, component }` or a switch on an active key) that wires `DepartmentManagement` and `CurrencySettings` into the sidebar. Identify that structure.

- [ ] **Step 3: Register the new section**

Following the exact pattern found in Step 2:
1. Add `import { UnitsOfMeasure } from './UnitsOfMeasure'` near the top of `AdminPanel.tsx`.
2. Import a suitable icon from `lucide-react` (e.g. `Ruler`) into the existing icon import block.
3. Add a nav entry for "Units of Measure" beside the Department Management entry, rendering `<UnitsOfMeasure />` for its section (match the key/label/icon/component shape used by the other entries).

- [ ] **Step 4: Typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors

- [ ] **Step 5: Manual verification**

Start the portal (with mdm-api running), open **Admin Panel → Units of Measure**. Confirm the 13 seeded units list. Add a unit (e.g. `g` / Gram / mass), edit it, deactivate one, and confirm: the new unit appears in EPMS New PR and OA Direct PA dropdowns; a deactivated unit disappears from those pickers; deleting a unit used by a part returns the 409 "used by N part(s)" message.

- [ ] **Step 6: Commit**

```bash
git add portal/src/pages/admin/UnitsOfMeasure.tsx portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(portal): Units of Measure admin section"
```

---

## Final verification

- [ ] **Backend test suites**

```bash
cd mdm-api && TEST_DATABASE_URL=$MDM_TEST_DATABASE_URL pytest tests/test_uom.py -v
cd expense-api && pytest tests/test_invoices.py -v
```
Expected: all pass.

- [ ] **All three frontends typecheck**

```bash
cd epms   && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
cd oa     && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```
Expected: no errors.

- [ ] **End-to-end smoke (services running: mdm-api, epms-api, expense-api, budget-api + the three SPAs)**
  - Portal admin shows 13 units; add/edit/deactivate works.
  - EPMS New PR + New PO Unit dropdowns reflect the master list.
  - EPMS Parts "Unit (UOM)" dropdown reflects the master list.
  - OA New Direct PA: Unit column present, defaults to first unit on manual lines, persists on submit, shows in preview.
  - Editing an existing PR whose saved unit was later deactivated still shows that unit selected.
