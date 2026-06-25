# Cross-System Data Maintenance Admin — Implementation Plan (Phase 1: Framework + EPMS)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a metadata-driven data-maintenance admin (browse / full-field edit / cascade delete) for EPMS documents (PR, PO, GR, Invoice, PA), surfaced in the Portal, gated to `system_admin` + a new `data_maintenance` permission, with cascade-impact preview and an audit log.

**Architecture:** A self-contained `app/admin/` module in **epms-api** exposes a uniform `/api/v1/admin/*` surface driven by an **entity registry** (field metadata + per-entity cascade handlers). Because all services share one physical `epms` database, cascade delete is a single in-DB transaction across tables (FK CASCADE for line items/attachments; explicit ordered deletes for RESTRICT children; explicit purge for polymorphic `tasks`/`approval_events`). The **Portal** gets one schema-driven UI that renders any entity the backend advertises.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + asyncpg + Alembic + pytest/pytest-asyncio (backend); React 19 + TypeScript 6 + @tanstack/react-query + Tailwind (portal frontend).

---

## Spec

Design doc: [docs/superpowers/specs/2026-06-08-data-maintenance-admin-design.md](../specs/2026-06-08-data-maintenance-admin-design.md)

## Key facts the implementer must know (verified against the codebase)

- **One shared DB.** epms-api, expense-api (OA), vms-api, approval-api, budget-api all use `postgresql+asyncpg://epms@10.10.50.20:5432/epms`. "Mirror" models map to the **same physical tables**. Deleting a row removes it everywhere at once.
- **FK `ondelete` directions** (from `app/models/*`):
  - RESTRICT (child blocks parent — delete child first): `purchase_orders.pr_id→purchase_requests`, `goods_receipts.po_id→purchase_orders`, `goods_receipts.pr_id→purchase_requests`, `invoices.po_id→purchase_orders`, `invoices.gr_id→goods_receipts`, `payment_applications.po_id→purchase_orders`.
  - CASCADE (auto-removed with parent): `*_line_items.*` and `*_attachment.*` (PR/PO/GR/PA each have a `*_attachment` table; **Invoice has no attachment table in epms-api**).
- **Polymorphic refs (no FK)**: `tasks(document_type, document_id)` and `approval_events(document_type, document_id)` with `document_type ∈ {pr, po, gr, invoice, pa}`. These do NOT cascade — purge explicitly.
- **Leaf-first delete order** for a full PR subtree: Invoices → PAs → GRs → PO → PR.
- **Access deps** live in `app/core/deps.py`: `require_roles("system_admin")`, `require_permission("<key>")` (system_admin always passes; otherwise checks the Access Control Matrix). `SessionDep`, `CurrentUserPayload` are the standard injected types.
- **Permission keys** are a flat list `PERMISSION_KEYS` in `app/crud/config.py:42`; `system_admin` automatically gets every key (`{k: True for k in PERMISSION_KEYS}` at line 215). `get_effective_role_permissions(cfg)` (line 359) merges stored overrides with defaults using `PERMISSION_KEYS`.
- **Router mounting**: each router is `APIRouter(prefix="/xxx")`, imported and `include_router`-ed in `app/api/v1/__init__.py`; the aggregate `api_router` is mounted with `settings.API_V1_PREFIX` in `app/main.py:98`.
- **Tests**: `tests/conftest.py` provides `client`, `admin_client` (system_admin), `finance_client` (finance_manager) HTTPX fixtures pointed at the `epms_test` DB. Run from `epms-api/` with the venv active: `python -m pytest tests/<file> -v`. Test DB must exist once: `python -m scripts.create_test_db`.
- **Frontend**: portal lives at `portal/`; existing admin page is `portal/src/pages/admin/AdminPanel.tsx`. Typecheck per repo convention: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`.

## Scope notes / deliberate decisions

- **Budget commitment release is deferred.** The spec lists budget-commitment cleanup in the cascade. Budget tables live in budget-api (separate Alembic, same DB) and are NOT imported by epms-api. Phase 1 cascade covers EPMS-owned tables + polymorphic `tasks`/`approval_events`. Budget reconciliation is handled when budget/OA entities are registered (later phase). Budget commitment rows have no FK to EPMS docs, so leaving them does not block deletes — it is a data-hygiene follow-up, called out here so it is not silently dropped.
- **Business guards are intentionally bypassed** (e.g. invoice "only unmatched/exception deletable"). Every action is audited.
- Phase 1 is **EPMS only**. OA (expense-api) and VMS (vms-api) reuse this framework in later specs.

## File Structure

**epms-api (create):**
- `app/admin/__init__.py` — package marker
- `app/admin/fields.py` — `FieldSpec`, `EntitySchema` dataclasses + serialization
- `app/admin/cascade.py` — generic helpers: purge polymorphic tasks/events; ordered delete helpers
- `app/admin/registry.py` — the 5 EPMS `EntitySpec`s (metadata + cascade callables)
- `app/admin/service.py` — list / get / edit / delete-preview / delete / bulk-delete + audit writes
- `app/api/v1/admin.py` — HTTP surface, access gating
- `app/models/admin_audit_log.py` — `AdminAuditLog` ORM model
- `migrations/versions/<rev>_add_admin_audit_log.py` — Alembic migration (revision id generated by alembic)
- `tests/test_admin.py` — backend test suite

**epms-api (modify):**
- `app/crud/config.py` — add `data_maintenance` to `PERMISSION_KEYS`
- `app/models/__init__.py` — register `AdminAuditLog`
- `app/api/v1/__init__.py` — import + include `admin_router`

**portal (create):**
- `portal/src/services/adminApi.ts` — typed client for `/admin/*`
- `portal/src/hooks/useAdmin.ts` — react-query hooks
- `portal/src/pages/admin/DataMaintenance.tsx` — system→entity→records→detail UI
- `portal/src/pages/admin/data-maintenance/EntityTable.tsx` — schema-driven table
- `portal/src/pages/admin/data-maintenance/RecordEditForm.tsx` — schema-driven edit form
- `portal/src/pages/admin/data-maintenance/DeleteConfirm.tsx` — preview + confirm dialog

**portal (modify):**
- the portal router/nav (locate during Task 13) — add a gated "Data Maintenance" route

---

## Task 1: Add the `data_maintenance` permission key

**Files:**
- Modify: `epms-api/app/crud/config.py:42-46`
- Test: `epms-api/tests/test_admin.py` (new)

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_admin.py`:

```python
"""Tests for the cross-system data-maintenance admin (Phase 1: EPMS)."""
import uuid

import pytest

from app.crud.config import PERMISSION_KEYS, DEFAULT_ROLE_PERMISSIONS


def test_data_maintenance_permission_registered():
    assert "data_maintenance" in PERMISSION_KEYS


def test_system_admin_has_data_maintenance_by_default():
    assert DEFAULT_ROLE_PERMISSIONS["system_admin"]["data_maintenance"] is True


def test_non_admin_lacks_data_maintenance_by_default():
    assert DEFAULT_ROLE_PERMISSIONS["ap_clerk"].get("data_maintenance", False) is False
```

> If the defaults map is named differently than `DEFAULT_ROLE_PERMISSIONS`, open `app/crud/config.py` around line 193-215 and use the actual name (the dict whose `"system_admin"` entry is `{k: True for k in PERMISSION_KEYS}`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_admin.py -v`
Expected: FAIL — `"data_maintenance"` not in `PERMISSION_KEYS`.

- [ ] **Step 3: Add the key**

In `app/crud/config.py`, edit the `PERMISSION_KEYS` list (line ~42) to append the new key on the admin line:

```python
PERMISSION_KEYS: list[str] = [
    "view_pr", "view_po", "view_gr", "view_invoice", "view_pa",
    "create_pr", "create_gr", "invoice_upload",
    "vendor_master", "parts_catalog", "admin_panel",
    "data_maintenance",
]
```

No other change is needed: `system_admin` defaults to `{k: True for k in PERMISSION_KEYS}`, and all other roles built via `_P(...)` default missing keys to `False`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_admin.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/crud/config.py tests/test_admin.py
git commit -m "feat(admin): add data_maintenance permission key"
```

---

## Task 2: `AdminAuditLog` model + migration

**Files:**
- Create: `epms-api/app/models/admin_audit_log.py`
- Modify: `epms-api/app/models/__init__.py`
- Create: `epms-api/migrations/versions/<rev>_add_admin_audit_log.py`
- Test: `epms-api/tests/test_admin.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_admin.py`:

```python
@pytest.mark.asyncio
async def test_admin_audit_log_model_persists(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.admin_audit_log import AdminAuditLog

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = AdminAuditLog(
            actor_id=uuid.uuid4(),
            actor_email="admin@example.com",
            action="delete",
            system="epms",
            entity="pr",
            record_id=uuid.uuid4(),
            record_number="PR-0001",
            before={"status": "draft"},
            after=None,
            cascade_summary={"purchase_orders": 1, "tasks": 2},
        )
        db.add(row)
        await db.commit()
        assert row.id is not None
        assert row.created_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_admin.py::test_admin_audit_log_model_persists -v`
Expected: FAIL — `ModuleNotFoundError: app.models.admin_audit_log`.

- [ ] **Step 3: Create the model**

Create `app/models/admin_audit_log.py`:

```python
"""Audit trail for the cross-system data-maintenance admin."""
from datetime import datetime
import uuid

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class AdminAuditLog(UUIDPrimaryKey, Base):
    __tablename__ = "admin_audit_log"

    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)        # edit | delete | bulk_delete
    system: Mapped[str] = mapped_column(String(20), nullable=False)        # epms | oa | vms
    entity: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    record_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cascade_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

> Confirm `UUIDPrimaryKey` and `Base` exist in `app/db/base.py` (they are used by every existing model, e.g. `app/models/pr.py:10`). If `UUIDPrimaryKey` provides the `id` column, do not redeclare it.

- [ ] **Step 4: Register the model**

In `app/models/__init__.py` add:

```python
from app.models.admin_audit_log import AdminAuditLog  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_admin.py::test_admin_audit_log_model_persists -v`
Expected: PASS (the session fixture calls `Base.metadata.create_all`, so the table is created in `epms_test`).

- [ ] **Step 6: Generate the Alembic migration for production**

Run from `epms-api/`:

```bash
alembic revision --autogenerate -m "add admin_audit_log"
```

Open the generated file in `migrations/versions/`. Verify `op.create_table("admin_audit_log", ...)` includes all columns above and `op.drop_table("admin_audit_log")` in `downgrade()`. Remove any unrelated autogenerated ops (the autogenerate may try to "fix" unrelated tables — delete those lines so the migration only touches `admin_audit_log`).

- [ ] **Step 7: Commit**

```bash
git add app/models/admin_audit_log.py app/models/__init__.py migrations/versions/
git commit -m "feat(admin): add admin_audit_log model and migration"
```

---

## Task 3: Field/schema dataclasses

**Files:**
- Create: `epms-api/app/admin/__init__.py`
- Create: `epms-api/app/admin/fields.py`
- Test: `epms-api/tests/test_admin.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_admin.py`:

```python
def test_field_spec_serialization():
    from app.admin.fields import FieldSpec, EntitySchema

    schema = EntitySchema(
        key="pr",
        label="Purchase Request",
        number_field="number",
        list_columns=["number", "title", "status", "amount"],
        search_fields=["number", "title"],
        order_by="created_at desc",
        fields=[
            FieldSpec(name="number", type="string", editable=False),
            FieldSpec(name="amount", type="decimal", editable=True),
            FieldSpec(name="status", type="enum", editable=True, options=["draft", "approved"]),
        ],
    )
    data = schema.to_dict()
    assert data["key"] == "pr"
    assert data["fields"][0] == {"name": "number", "type": "string", "editable": False,
                                 "label": "number", "options": None}
    assert data["fields"][2]["options"] == ["draft", "approved"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_admin.py::test_field_spec_serialization -v`
Expected: FAIL — `ModuleNotFoundError: app.admin.fields`.

- [ ] **Step 3: Implement**

Create `app/admin/__init__.py` (empty file).

Create `app/admin/fields.py`:

```python
"""Schema dataclasses describing how a managed entity is rendered/edited."""
from __future__ import annotations

from dataclasses import dataclass, field

FieldType = str  # "string"|"number"|"decimal"|"bool"|"date"|"datetime"|"uuid"|"json"|"enum"


@dataclass
class FieldSpec:
    name: str
    type: FieldType
    editable: bool
    label: str | None = None
    options: list[str] | None = None  # for enum types

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "editable": self.editable,
            "label": self.label or self.name,
            "options": self.options,
        }


@dataclass
class EntitySchema:
    key: str
    label: str
    number_field: str
    list_columns: list[str]
    search_fields: list[str]
    order_by: str
    fields: list[FieldSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "number_field": self.number_field,
            "list_columns": self.list_columns,
            "search_fields": self.search_fields,
            "order_by": self.order_by,
            "fields": [f.to_dict() for f in self.fields],
        }

    def editable_field_names(self) -> set[str]:
        return {f.name for f in self.fields if f.editable}

    def field_type(self, name: str) -> str | None:
        for f in self.fields:
            if f.name == name:
                return f.type
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_admin.py::test_field_spec_serialization -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/admin/__init__.py app/admin/fields.py tests/test_admin.py
git commit -m "feat(admin): add entity schema dataclasses"
```

---

## Task 4: Cascade helpers

**Files:**
- Create: `epms-api/app/admin/cascade.py`
- Test: `epms-api/tests/test_admin.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_admin.py`:

```python
@pytest.mark.asyncio
async def test_purge_polymorphic_counts_and_deletes(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select
    from app.models.task import Task
    from app.admin.cascade import count_polymorphic, delete_polymorphic

    doc_id = uuid.uuid4()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(Task(document_type="pr", document_id=doc_id, document_number="PR-9", status="pending"))
        db.add(Task(document_type="pr", document_id=doc_id, document_number="PR-9", status="pending"))
        db.add(Task(document_type="po", document_id=uuid.uuid4(), document_number="PO-1", status="pending"))
        await db.commit()

        n = await count_polymorphic(db, Task, "pr", doc_id)
        assert n == 2
        await delete_polymorphic(db, Task, "pr", doc_id)
        await db.commit()
        remaining = (await db.execute(
            select(Task).where(Task.document_type == "pr", Task.document_id == doc_id)
        )).scalars().all()
        assert remaining == []
```

> The `Task` constructor args must match `app/models/task.py`. Open it and include any other NOT NULL columns (e.g. an `assigned_user_id` is nullable per inspection; `status` may be required). Adjust the test kwargs to satisfy NOT NULL constraints.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_admin.py::test_purge_polymorphic_counts_and_deletes -v`
Expected: FAIL — `ModuleNotFoundError: app.admin.cascade`.

- [ ] **Step 3: Implement**

Create `app/admin/cascade.py`:

```python
"""Generic cascade helpers shared by entity cascade handlers."""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalEvent
from app.models.task import Task


async def count_polymorphic(db: AsyncSession, model, document_type: str, document_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(
        model.document_type == document_type, model.document_id == document_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def delete_polymorphic(db: AsyncSession, model, document_type: str, document_id: uuid.UUID) -> int:
    n = await count_polymorphic(db, model, document_type, document_id)
    await db.execute(
        delete(model).where(model.document_type == document_type, model.document_id == document_id)
    )
    return n


async def purge_workflow_refs(db: AsyncSession, document_type: str, document_id: uuid.UUID) -> dict[str, int]:
    """Delete tasks + approval_events that reference a document polymorphically.
    Returns counts for the cascade summary."""
    tasks = await delete_polymorphic(db, Task, document_type, document_id)
    events = await delete_polymorphic(db, ApprovalEvent, document_type, document_id)
    return {"tasks": tasks, "approval_events": events}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_admin.py::test_purge_polymorphic_counts_and_deletes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/admin/cascade.py tests/test_admin.py
git commit -m "feat(admin): add polymorphic cascade helpers"
```

---

## Task 5: Entity registry with cascade handlers

**Files:**
- Create: `epms-api/app/admin/registry.py`
- Test: `epms-api/tests/test_admin.py`

Each entity gets an `EntitySpec` (schema + model + async `cascade_preview` + `cascade_delete`). Cascade functions assume rows are loaded; they MUST run inside the caller's transaction. Order encodes leaf-first deletion to satisfy RESTRICT FKs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin.py`:

```python
def test_registry_has_five_epms_entities():
    from app.admin.registry import REGISTRY
    assert set(REGISTRY.keys()) == {"pr", "po", "gr", "invoice", "pa"}
    for spec in REGISTRY.values():
        assert spec.schema.number_field
        assert spec.schema.fields            # non-empty
        assert callable(spec.cascade_delete)
        assert callable(spec.cascade_preview)


@pytest.mark.asyncio
async def test_pr_cascade_deletes_subtree_and_workflow_refs(test_engine, admin_client):
    """Create PR→PO→GR→Invoice + tasks, then cascade-delete the PR; everything gone."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select, func
    from decimal import Decimal
    from datetime import date
    from app.models.pr import PurchaseRequest
    from app.models.po import PurchaseOrder
    from app.models.gr import GoodsReceipt
    from app.models.invoice import Invoice
    from app.models.task import Task
    from app.admin.registry import REGISTRY

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pr_id = uuid.uuid4(); po_id = uuid.uuid4(); gr_id = uuid.uuid4()
    async with factory() as db:
        # Minimal valid rows — fill every NOT NULL column per the models.
        creator = uuid.uuid4()
        db.add(PurchaseRequest(id=pr_id, number="PR-T1", title="t", type=1, status="approved",
                               currency="CAD", amount=Decimal("10"), created_by=creator, po_id=po_id))
        db.add(PurchaseOrder(id=po_id, number="PO-T1", status="approved", currency="CAD",
                             amount=Decimal("10"), pr_id=pr_id, created_by=creator))
        db.add(GoodsReceipt(id=gr_id, number="GR-T1", status="confirmed",
                            po_id=po_id, pr_id=pr_id, created_by=creator))
        db.add(Invoice(id=uuid.uuid4(), number="INV-T1", status="unmatched",
                       po_id=po_id, gr_id=gr_id, amount=Decimal("10"), currency="CAD"))
        db.add(Task(document_type="pr", document_id=pr_id, document_number="PR-T1", status="pending"))
        await db.commit()

    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        summary = await REGISTRY["pr"].cascade_delete(db, pr)
        await db.commit()
        assert summary["purchase_orders"] >= 1
        assert summary["goods_receipts"] >= 1
        assert summary["invoices"] >= 1
        assert summary["tasks"] >= 1

    async with factory() as db:
        for model in (PurchaseRequest, PurchaseOrder, GoodsReceipt, Invoice):
            cnt = (await db.execute(select(func.count()).select_from(model))).scalar_one()
            assert cnt == 0, f"{model.__name__} not fully deleted"
```

> Before running, open `app/models/{pr,po,gr,invoice,pa}.py` and ensure the test's constructor kwargs satisfy every `nullable=False` column. Add any missing required fields (the inspection showed PR/PO need `currency`, `amount`, `created_by`; GR/Invoice need their FKs + `status`). Adjust as needed — the test must insert valid rows.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin.py -k "registry or cascade_deletes_subtree" -v`
Expected: FAIL — `ModuleNotFoundError: app.admin.registry`.

- [ ] **Step 3: Implement the registry**

Create `app/admin/registry.py`:

```python
"""EPMS entity registry: schema metadata + cascade handlers (Phase 1)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.cascade import purge_workflow_refs
from app.admin.fields import EntitySchema, FieldSpec
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest

CascadeFn = Callable[[AsyncSession, object], Awaitable[dict[str, int]]]


@dataclass
class EntitySpec:
    schema: EntitySchema
    model: type
    system: str
    cascade_preview: CascadeFn  # counts only, no mutation
    cascade_delete: CascadeFn   # deletes child subtree + workflow refs; caller commits


# ── helpers to fetch dependents ────────────────────────────────────────────────

async def _children(db: AsyncSession, model, attr, value):
    return (await db.execute(select(model).where(getattr(model, attr) == value))).scalars().all()


# ── Invoice (leaf) ─────────────────────────────────────────────────────────────

async def _invoice_delete(db: AsyncSession, inv) -> dict[str, int]:
    summary = await purge_workflow_refs(db, "invoice", inv.id)
    await db.delete(inv)          # invoice has no children in epms
    summary["invoices"] = 1
    return summary

async def _invoice_preview(db: AsyncSession, inv) -> dict[str, int]:
    from app.admin.cascade import count_polymorphic
    from app.models.task import Task
    from app.models.approval import ApprovalEvent
    return {
        "invoices": 1,
        "tasks": await count_polymorphic(db, Task, "invoice", inv.id),
        "approval_events": await count_polymorphic(db, ApprovalEvent, "invoice", inv.id),
    }


# ── PA ──────────────────────────────────────────────────────────────────────────

async def _pa_delete(db: AsyncSession, pa) -> dict[str, int]:
    summary = await purge_workflow_refs(db, "pa", pa.id)
    await db.delete(pa)           # pa_line_items + pa_attachment cascade via FK
    summary["payment_applications"] = 1
    return summary

async def _pa_preview(db: AsyncSession, pa) -> dict[str, int]:
    from app.admin.cascade import count_polymorphic
    from app.models.task import Task
    from app.models.approval import ApprovalEvent
    return {
        "payment_applications": 1,
        "tasks": await count_polymorphic(db, Task, "pa", pa.id),
        "approval_events": await count_polymorphic(db, ApprovalEvent, "pa", pa.id),
    }


# ── GR (blocked by Invoice.gr_id) ────────────────────────────────────────────────

def _merge(into: dict, add: dict) -> dict:
    for k, v in add.items():
        into[k] = into.get(k, 0) + v
    return into

async def _gr_delete(db: AsyncSession, gr) -> dict[str, int]:
    summary: dict[str, int] = {}
    for inv in await _children(db, Invoice, "gr_id", gr.id):
        _merge(summary, await _invoice_delete(db, inv))
    _merge(summary, await purge_workflow_refs(db, "gr", gr.id))
    await db.delete(gr)           # gr_line_items + gr_attachment cascade via FK
    _merge(summary, {"goods_receipts": 1})
    return summary

async def _gr_preview(db: AsyncSession, gr) -> dict[str, int]:
    summary: dict[str, int] = {"goods_receipts": 1}
    for inv in await _children(db, Invoice, "gr_id", gr.id):
        _merge(summary, await _invoice_preview(db, inv))
    from app.admin.cascade import count_polymorphic
    from app.models.task import Task
    from app.models.approval import ApprovalEvent
    _merge(summary, {
        "tasks": await count_polymorphic(db, Task, "gr", gr.id),
        "approval_events": await count_polymorphic(db, ApprovalEvent, "gr", gr.id),
    })
    return summary


# ── PO (blocked by GR/Invoice/PA on po_id) ───────────────────────────────────────

async def _po_delete(db: AsyncSession, po) -> dict[str, int]:
    summary: dict[str, int] = {}
    for inv in await _children(db, Invoice, "po_id", po.id):
        _merge(summary, await _invoice_delete(db, inv))
    for pa in await _children(db, PaymentApplication, "po_id", po.id):
        _merge(summary, await _pa_delete(db, pa))
    for gr in await _children(db, GoodsReceipt, "po_id", po.id):
        _merge(summary, await _gr_delete(db, gr))
    # clear originating PR back-reference so it doesn't dangle
    for pr in await _children(db, PurchaseRequest, "po_id", po.id):
        pr.po_id = None
        pr.po_number = None
    _merge(summary, await purge_workflow_refs(db, "po", po.id))
    await db.delete(po)           # po_line_items + po_attachment cascade via FK
    _merge(summary, {"purchase_orders": 1})
    return summary

async def _po_preview(db: AsyncSession, po) -> dict[str, int]:
    summary: dict[str, int] = {"purchase_orders": 1}
    for inv in await _children(db, Invoice, "po_id", po.id):
        _merge(summary, await _invoice_preview(db, inv))
    for pa in await _children(db, PaymentApplication, "po_id", po.id):
        _merge(summary, await _pa_preview(db, pa))
    for gr in await _children(db, GoodsReceipt, "po_id", po.id):
        _merge(summary, await _gr_preview(db, gr))
    from app.admin.cascade import count_polymorphic
    from app.models.task import Task
    from app.models.approval import ApprovalEvent
    _merge(summary, {
        "tasks": await count_polymorphic(db, Task, "po", po.id),
        "approval_events": await count_polymorphic(db, ApprovalEvent, "po", po.id),
    })
    return summary


# ── PR (blocked by PO.pr_id, GR.pr_id) ───────────────────────────────────────────

async def _pr_delete(db: AsyncSession, pr) -> dict[str, int]:
    summary: dict[str, int] = {}
    for po in await _children(db, PurchaseOrder, "pr_id", pr.id):
        _merge(summary, await _po_delete(db, po))
    # GRs tied directly to the PR but not via a (now-deleted) PO
    for gr in await _children(db, GoodsReceipt, "pr_id", pr.id):
        _merge(summary, await _gr_delete(db, gr))
    _merge(summary, await purge_workflow_refs(db, "pr", pr.id))
    await db.delete(pr)           # pr_line_items + pr_attachment cascade via FK
    _merge(summary, {"purchase_requests": 1})
    return summary

async def _pr_preview(db: AsyncSession, pr) -> dict[str, int]:
    summary: dict[str, int] = {"purchase_requests": 1}
    for po in await _children(db, PurchaseOrder, "pr_id", pr.id):
        _merge(summary, await _po_preview(db, po))
    for gr in await _children(db, GoodsReceipt, "pr_id", pr.id):
        _merge(summary, await _gr_preview(db, gr))
    from app.admin.cascade import count_polymorphic
    from app.models.task import Task
    from app.models.approval import ApprovalEvent
    _merge(summary, {
        "tasks": await count_polymorphic(db, Task, "pr", pr.id),
        "approval_events": await count_polymorphic(db, ApprovalEvent, "pr", pr.id),
    })
    return summary


# ── Schemas ──────────────────────────────────────────────────────────────────────
# NOTE: keep `fields` aligned with the model columns. Mark identity/number/created_by
# and computed totals as editable=False. Everything a maintainer may legitimately
# correct (status, amounts, names, notes, dates) is editable=True.

_PR_SCHEMA = EntitySchema(
    key="pr", label="Purchase Request", number_field="number",
    list_columns=["number", "title", "status", "amount", "vendor_name", "created_at"],
    search_fields=["number", "title", "vendor_name"], order_by="created_at desc",
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("status", "string", True),
        FieldSpec("type", "number", True),
        FieldSpec("currency", "string", True),
        FieldSpec("amount", "decimal", True),
        FieldSpec("vendor_name", "string", True),
        FieldSpec("department_name", "string", True),
        FieldSpec("budget_code", "string", True),
        FieldSpec("notes", "string", True),
        FieldSpec("required_by", "date", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_PO_SCHEMA = EntitySchema(
    key="po", label="Purchase Order", number_field="number",
    list_columns=["number", "status", "amount", "vendor_name", "created_at"],
    search_fields=["number", "vendor_name"], order_by="created_at desc",
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("status", "string", True),
        FieldSpec("currency", "string", True),
        FieldSpec("amount", "decimal", True),
        FieldSpec("vendor_name", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_GR_SCHEMA = EntitySchema(
    key="gr", label="Goods Receipt", number_field="number",
    list_columns=["number", "status", "created_at"],
    search_fields=["number"], order_by="created_at desc",
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("status", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_INVOICE_SCHEMA = EntitySchema(
    key="invoice", label="Invoice", number_field="number",
    list_columns=["number", "status", "amount", "currency", "created_at"],
    search_fields=["number"], order_by="created_at desc",
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("status", "string", True),
        FieldSpec("currency", "string", True),
        FieldSpec("amount", "decimal", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_PA_SCHEMA = EntitySchema(
    key="pa", label="Payment Application", number_field="number",
    list_columns=["number", "status", "amount", "created_at"],
    search_fields=["number"], order_by="created_at desc",
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("status", "string", True),
        FieldSpec("amount", "decimal", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

REGISTRY: dict[str, EntitySpec] = {
    "pr": EntitySpec(_PR_SCHEMA, PurchaseRequest, "epms", _pr_preview, _pr_delete),
    "po": EntitySpec(_PO_SCHEMA, PurchaseOrder, "epms", _po_preview, _po_delete),
    "gr": EntitySpec(_GR_SCHEMA, GoodsReceipt, "epms", _gr_preview, _gr_delete),
    "invoice": EntitySpec(_INVOICE_SCHEMA, Invoice, "epms", _invoice_preview, _invoice_delete),
    "pa": EntitySpec(_PA_SCHEMA, PaymentApplication, "epms", _pa_preview, _pa_delete),
}
```

> Verify each schema's `fields`/`list_columns`/`number_field` against the actual model columns. Adjust names that differ (e.g. if PA's number column is not `number`). The cascade logic does not depend on the schema, so cascade tests pass independently of field tuning.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_admin.py -k "registry or cascade_deletes_subtree" -v`
Expected: PASS. If the subtree test fails on a NOT NULL violation, fix the test's row construction (not the registry).

- [ ] **Step 5: Commit**

```bash
git add app/admin/registry.py tests/test_admin.py
git commit -m "feat(admin): EPMS entity registry with cascade handlers"
```

---

## Task 6: Admin service — list, get, edit (+ audit)

**Files:**
- Create: `epms-api/app/admin/service.py`
- Test: `epms-api/tests/test_admin.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_admin.py`:

```python
@pytest.mark.asyncio
async def test_service_list_get_and_edit_writes_audit(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select
    from decimal import Decimal
    from app.models.pr import PurchaseRequest
    from app.models.admin_audit_log import AdminAuditLog
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pr_id = uuid.uuid4(); actor = uuid.uuid4()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-S1", title="orig", type=1, status="draft",
                               currency="CAD", amount=Decimal("5"), created_by=actor))
        await db.commit()

    async with factory() as db:
        rows, total = await service.list_records(db, "pr", page=1, page_size=20, search=None)
        assert total >= 1
        rec = await service.get_record(db, "pr", pr_id)
        assert rec["number"] == "PR-S1"

        updated = await service.edit_record(
            db, "pr", pr_id, {"title": "fixed", "amount": "9.50"},
            actor_id=actor, actor_email="a@x.com",
        )
        await db.commit()
        assert updated["title"] == "fixed"

    async with factory() as db:
        fresh = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert fresh.title == "fixed"
        assert str(fresh.amount) == "9.50"
        audits = (await db.execute(select(AdminAuditLog).where(AdminAuditLog.action == "edit"))).scalars().all()
        assert len(audits) == 1
        assert audits[0].before["title"] == "orig"
        assert audits[0].after["title"] == "fixed"


@pytest.mark.asyncio
async def test_edit_rejects_non_editable_field(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from decimal import Decimal
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pr_id = uuid.uuid4(); actor = uuid.uuid4()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-S2", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("5"), created_by=actor))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ValueError):
            await service.edit_record(db, "pr", pr_id, {"number": "HACK"},
                                      actor_id=actor, actor_email="a@x.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin.py -k "service_list_get_and_edit or rejects_non_editable" -v`
Expected: FAIL — `ModuleNotFoundError: app.admin.service`.

- [ ] **Step 3: Implement**

Create `app/admin/service.py`:

```python
"""Generic CRUD + audit over registered entities."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.registry import REGISTRY, EntitySpec
from app.models.admin_audit_log import AdminAuditLog


def _spec(entity: str) -> EntitySpec:
    spec = REGISTRY.get(entity)
    if spec is None:
        raise ValueError(f"Unknown entity '{entity}'")
    return spec


def _serialize(spec: EntitySpec, row) -> dict:
    out: dict = {"id": str(getattr(row, "id"))}
    for f in spec.schema.fields:
        v = getattr(row, f.name, None)
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, uuid.UUID):
            v = str(v)
        out[f.name] = v
    return out


def _coerce(field_type: str, value):
    if value is None:
        return None
    if field_type == "decimal":
        return Decimal(str(value))
    if field_type == "number":
        return int(value)
    if field_type == "bool":
        return bool(value)
    if field_type == "date":
        return date.fromisoformat(value) if isinstance(value, str) else value
    if field_type == "datetime":
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    return value


async def list_records(db: AsyncSession, entity: str, *, page: int, page_size: int,
                       search: str | None) -> tuple[list[dict], int]:
    spec = _spec(entity)
    model = spec.model
    stmt = select(model)
    if search and spec.schema.search_fields:
        clauses = [getattr(model, f).ilike(f"%{search}%") for f in spec.schema.search_fields]
        stmt = stmt.where(or_(*clauses))
    total = int((await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one())
    col, _, direction = spec.schema.order_by.partition(" ")
    order = getattr(model, col)
    stmt = stmt.order_by(order.desc() if direction.lower() == "desc" else order.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize(spec, r) for r in rows], total


async def get_record(db: AsyncSession, entity: str, record_id: uuid.UUID) -> dict | None:
    spec = _spec(entity)
    row = (await db.execute(select(spec.model).where(spec.model.id == record_id))).scalar_one_or_none()
    return _serialize(spec, row) if row else None


async def _load(db: AsyncSession, spec: EntitySpec, record_id: uuid.UUID):
    row = (await db.execute(select(spec.model).where(spec.model.id == record_id))).scalar_one_or_none()
    if row is None:
        raise ValueError("Record not found")
    return row


async def edit_record(db: AsyncSession, entity: str, record_id: uuid.UUID, patch: dict,
                      *, actor_id: uuid.UUID, actor_email: str) -> dict:
    spec = _spec(entity)
    row = await _load(db, spec, record_id)
    editable = spec.schema.editable_field_names()
    before = _serialize(spec, row)
    for key, value in patch.items():
        if key not in editable:
            raise ValueError(f"Field '{key}' is not editable")
        setattr(row, key, _coerce(spec.schema.field_type(key), value))
    await db.flush()
    after = _serialize(spec, row)
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="edit", system=spec.system,
        entity=entity, record_id=record_id,
        record_number=str(getattr(row, spec.schema.number_field, None)),
        before={k: before[k] for k in patch if k in before},
        after={k: after[k] for k in patch if k in after},
    ))
    await db.flush()
    return after
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_admin.py -k "service_list_get_and_edit or rejects_non_editable" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/admin/service.py tests/test_admin.py
git commit -m "feat(admin): generic list/get/edit service with audit"
```

---

## Task 7: Admin service — delete preview, delete, bulk-delete (+ audit)

**Files:**
- Modify: `epms-api/app/admin/service.py`
- Test: `epms-api/tests/test_admin.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_admin.py`:

```python
@pytest.mark.asyncio
async def test_delete_preview_then_delete_with_audit(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select, func
    from decimal import Decimal
    from app.models.pr import PurchaseRequest
    from app.models.admin_audit_log import AdminAuditLog
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pr_id = uuid.uuid4(); actor = uuid.uuid4()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-D1", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("5"), created_by=actor))
        await db.commit()

    async with factory() as db:
        preview = await service.delete_preview(db, "pr", pr_id)
        assert preview["purchase_requests"] == 1

    async with factory() as db:
        summary = await service.delete_record(db, "pr", pr_id,
                                               actor_id=actor, actor_email="a@x.com")
        await db.commit()
        assert summary["purchase_requests"] == 1

    async with factory() as db:
        cnt = (await db.execute(select(func.count()).select_from(PurchaseRequest))).scalar_one()
        assert cnt == 0
        adel = (await db.execute(select(AdminAuditLog).where(AdminAuditLog.action == "delete"))).scalars().all()
        assert len(adel) == 1
        assert adel[0].cascade_summary["purchase_requests"] == 1
        assert adel[0].before["number"] == "PR-D1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_admin.py::test_delete_preview_then_delete_with_audit -v`
Expected: FAIL — `AttributeError: module 'app.admin.service' has no attribute 'delete_preview'`.

- [ ] **Step 3: Implement (append to `app/admin/service.py`)**

```python
async def delete_preview(db: AsyncSession, entity: str, record_id: uuid.UUID) -> dict[str, int]:
    spec = _spec(entity)
    row = await _load(db, spec, record_id)
    return await spec.cascade_preview(db, row)


async def delete_record(db: AsyncSession, entity: str, record_id: uuid.UUID,
                        *, actor_id: uuid.UUID, actor_email: str) -> dict[str, int]:
    spec = _spec(entity)
    row = await _load(db, spec, record_id)
    snapshot = _serialize(spec, row)
    number = str(getattr(row, spec.schema.number_field, None))
    summary = await spec.cascade_delete(db, row)
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="delete", system=spec.system,
        entity=entity, record_id=record_id, record_number=number,
        before=snapshot, after=None, cascade_summary=summary,
    ))
    await db.flush()
    return summary


async def bulk_delete(db: AsyncSession, entity: str, record_ids: list[uuid.UUID],
                      *, actor_id: uuid.UUID, actor_email: str) -> dict[str, int]:
    total: dict[str, int] = {}
    for rid in record_ids:
        summary = await delete_record(db, entity, rid, actor_id=actor_id, actor_email=actor_email)
        for k, v in summary.items():
            total[k] = total.get(k, 0) + v
    # one bulk_delete audit row summarizing the batch
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="bulk_delete", system=_spec(entity).system,
        entity=entity, record_id=record_ids[0] if record_ids else uuid.uuid4(),
        record_number=f"{len(record_ids)} records", before=None, after=None, cascade_summary=total,
    ))
    await db.flush()
    return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_admin.py::test_delete_preview_then_delete_with_audit -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/admin/service.py tests/test_admin.py
git commit -m "feat(admin): cascade delete preview/delete/bulk-delete with audit"
```

---

## Task 8: HTTP surface + access gating + router mount

**Files:**
- Create: `epms-api/app/api/v1/admin.py`
- Modify: `epms-api/app/api/v1/__init__.py`
- Test: `epms-api/tests/test_admin.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin.py`:

```python
@pytest.mark.asyncio
async def test_entities_endpoint_requires_permission(client):
    r = await client.get("/api/v1/admin/entities")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_admin_can_list_entities(admin_client):
    r = await admin_client.get("/api/v1/admin/entities")
    assert r.status_code == 200
    keys = {e["key"] for e in r.json()}
    assert {"pr", "po", "gr", "invoice", "pa"} <= keys


@pytest.mark.asyncio
async def test_admin_list_records_endpoint(admin_client):
    r = await admin_client.get("/api/v1/admin/pr?page=1&page_size=10")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin.py -k "entities_endpoint or admin_can_list or list_records_endpoint" -v`
Expected: FAIL — 404 (router not mounted).

- [ ] **Step 3: Implement the router**

Create `app/api/v1/admin.py`:

```python
"""Cross-system data-maintenance admin endpoints (Phase 1: EPMS)."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.admin import service
from app.admin.registry import REGISTRY
from app.core.deps import CurrentUserPayload, SessionDep, require_permission

router = APIRouter(prefix="/admin", tags=["data-maintenance"])

AdminUser = Annotated[dict, Depends(require_permission("data_maintenance"))]


class ListResponse(BaseModel):
    items: list[dict]
    total: int


class BulkDeleteRequest(BaseModel):
    ids: list[uuid.UUID]


def _actor(user: dict) -> tuple[uuid.UUID, str]:
    return uuid.UUID(user["sub"]), user.get("email", "")


@router.get("/entities")
async def list_entities(user: AdminUser):
    return [spec.schema.to_dict() | {"system": spec.system} for spec in REGISTRY.values()]


@router.get("/{entity}", response_model=ListResponse)
async def list_records(entity: str, db: SessionDep, user: AdminUser,
                       page: int = Query(1, ge=1), page_size: int = Query(20, le=200),
                       search: str | None = Query(None)):
    try:
        items, total = await service.list_records(db, entity, page=page, page_size=page_size, search=search)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return ListResponse(items=items, total=total)


@router.get("/{entity}/{record_id}")
async def get_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser):
    try:
        rec = await service.get_record(db, entity, record_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if rec is None:
        raise HTTPException(404, "Record not found")
    return rec


@router.patch("/{entity}/{record_id}")
async def edit_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                      patch: dict = Body(...)):
    actor_id, email = _actor(user)
    try:
        result = await service.edit_record(db, entity, record_id, patch, actor_id=actor_id, actor_email=email)
        await db.commit()
        return result
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))


@router.delete("/{entity}/{record_id}")
async def delete_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                        preview: int = Query(0)):
    actor_id, email = _actor(user)
    try:
        if preview:
            summary = await service.delete_preview(db, entity, record_id)
            return {"preview": True, "cascade": summary}
        summary = await service.delete_record(db, entity, record_id, actor_id=actor_id, actor_email=email)
        await db.commit()
        return {"preview": False, "cascade": summary}
    except ValueError as e:
        await db.rollback()
        raise HTTPException(404, str(e))


@router.post("/{entity}/bulk-delete")
async def bulk_delete(entity: str, db: SessionDep, user: AdminUser, body: BulkDeleteRequest):
    actor_id, email = _actor(user)
    try:
        summary = await service.bulk_delete(db, entity, body.ids, actor_id=actor_id, actor_email=email)
        await db.commit()
        return {"deleted": len(body.ids), "cascade": summary}
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
```

> Verify the JWT payload key for the user id is `"sub"` and that email is present as `"email"` (check `app/core/security.py::create_access_token` and `decode_token`). If email is not in the token, fetch it from the users table by `sub`, or store `""`.

- [ ] **Step 4: Mount the router**

In `app/api/v1/__init__.py`, add the import alongside the others and include it:

```python
from app.api.v1.admin import router as admin_router
```

and after the other `include_router` calls:

```python
api_router.include_router(admin_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_admin.py -k "entities_endpoint or admin_can_list or list_records_endpoint" -v`
Expected: PASS.

- [ ] **Step 6: Run the whole admin suite**

Run: `python -m pytest tests/test_admin.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/api/v1/admin.py app/api/v1/__init__.py tests/test_admin.py
git commit -m "feat(admin): /admin HTTP endpoints with permission gating"
```

---

## Task 9: Portal API client + hooks

**Files:**
- Create: `portal/src/services/adminApi.ts`
- Create: `portal/src/hooks/useAdmin.ts`

> First locate the portal's existing API client to reuse its base-URL + auth-header convention (search `portal/src/services` or `portal/src/lib` for an `axios`/`fetch` wrapper, and the EPMS service base URL the portal uses). The code below assumes a shared `api` axios-like instance exporting `.get/.patch/.delete/.post`. Adapt imports to the actual client. Per memory: portal/OA call services with an absolute `VITE_API_URL`; do not use relative `/api` paths.

- [ ] **Step 1: Implement the typed client**

Create `portal/src/services/adminApi.ts`:

```typescript
import { api } from '@/lib/api' // adapt to the portal's actual api client module

export interface FieldSpec {
  name: string
  type: 'string' | 'number' | 'decimal' | 'bool' | 'date' | 'datetime' | 'uuid' | 'json' | 'enum'
  editable: boolean
  label: string
  options: string[] | null
}

export interface EntitySchema {
  key: string
  label: string
  system: string
  number_field: string
  list_columns: string[]
  search_fields: string[]
  order_by: string
  fields: FieldSpec[]
}

export interface ListResult { items: Record<string, unknown>[]; total: number }
export type CascadeSummary = Record<string, number>

export const adminApi = {
  entities: () => api.get<EntitySchema[]>('/api/v1/admin/entities').then(r => r.data),
  list: (entity: string, params: { page: number; page_size: number; search?: string }) =>
    api.get<ListResult>(`/api/v1/admin/${entity}`, { params }).then(r => r.data),
  get: (entity: string, id: string) =>
    api.get<Record<string, unknown>>(`/api/v1/admin/${entity}/${id}`).then(r => r.data),
  edit: (entity: string, id: string, patch: Record<string, unknown>) =>
    api.patch<Record<string, unknown>>(`/api/v1/admin/${entity}/${id}`, patch).then(r => r.data),
  preview: (entity: string, id: string) =>
    api.delete<{ preview: boolean; cascade: CascadeSummary }>(
      `/api/v1/admin/${entity}/${id}`, { params: { preview: 1 } }).then(r => r.data.cascade),
  remove: (entity: string, id: string) =>
    api.delete<{ cascade: CascadeSummary }>(`/api/v1/admin/${entity}/${id}`).then(r => r.data.cascade),
  bulkDelete: (entity: string, ids: string[]) =>
    api.post<{ deleted: number; cascade: CascadeSummary }>(
      `/api/v1/admin/${entity}/bulk-delete`, { ids }).then(r => r.data),
}
```

- [ ] **Step 2: Implement react-query hooks**

Create `portal/src/hooks/useAdmin.ts`:

```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { adminApi } from '@/services/adminApi'

export const useAdminEntities = () =>
  useQuery({ queryKey: ['admin', 'entities'], queryFn: adminApi.entities })

export const useAdminList = (entity: string, page: number, search: string) =>
  useQuery({
    queryKey: ['admin', entity, page, search],
    queryFn: () => adminApi.list(entity, { page, page_size: 20, search: search || undefined }),
    enabled: !!entity,
  })

export const useAdminEdit = (entity: string) => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, unknown> }) =>
      adminApi.edit(entity, id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', entity] }),
  })
}

export const useAdminDelete = (entity: string) => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => adminApi.remove(entity, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', entity] }),
  })
}
```

- [ ] **Step 3: Typecheck**

Run from `portal/`: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors in the new files (fix import paths if the api client lives elsewhere).

- [ ] **Step 4: Commit**

```bash
git add portal/src/services/adminApi.ts portal/src/hooks/useAdmin.ts
git commit -m "feat(portal): admin data-maintenance API client and hooks"
```

---

## Task 10: Schema-driven records table

**Files:**
- Create: `portal/src/pages/admin/data-maintenance/EntityTable.tsx`

- [ ] **Step 1: Implement**

Create `portal/src/pages/admin/data-maintenance/EntityTable.tsx`:

```tsx
import { useState } from 'react'
import type { EntitySchema } from '@/services/adminApi'
import { useAdminList } from '@/hooks/useAdmin'

interface Props {
  schema: EntitySchema
  onEdit: (record: Record<string, unknown>) => void
  onDelete: (record: Record<string, unknown>) => void
}

export function EntityTable({ schema, onEdit, onDelete }: Props) {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const { data, isLoading } = useAdminList(schema.key, page, search)

  const cols = schema.list_columns
  const totalPages = data ? Math.max(1, Math.ceil(data.total / 20)) : 1

  return (
    <div className="flex flex-col gap-3">
      <input
        value={search}
        onChange={(e) => { setSearch(e.target.value); setPage(1) }}
        placeholder={`Search ${schema.label}…`}
        className="h-9 w-72 rounded-lg border border-neutral-300 px-3 text-sm"
      />
      <div className="overflow-x-auto rounded-xl border border-neutral-200">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50">
            <tr>
              {cols.map((c) => (
                <th key={c} className="px-3 py-2 text-left text-xs font-semibold uppercase text-neutral-500">{c}</th>
              ))}
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td colSpan={cols.length + 1} className="px-3 py-6 text-center text-neutral-400">Loading…</td></tr>}
            {data?.items.map((row) => (
              <tr key={String(row.id)} className="border-t border-neutral-100">
                {cols.map((c) => (
                  <td key={c} className="px-3 py-2 text-neutral-800">{String(row[c] ?? '')}</td>
                ))}
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  <button onClick={() => onEdit(row)} className="text-xs text-primary-600 hover:underline mr-3">Edit</button>
                  <button onClick={() => onDelete(row)} className="text-xs text-danger-600 hover:underline">Delete</button>
                </td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr><td colSpan={cols.length + 1} className="px-3 py-6 text-center text-neutral-400">No records</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="rounded border px-2 py-1 disabled:opacity-40">Prev</button>
        <span>Page {page} / {totalPages} · {data?.total ?? 0} total</span>
        <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)} className="rounded border px-2 py-1 disabled:opacity-40">Next</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run from `portal/`: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add portal/src/pages/admin/data-maintenance/EntityTable.tsx
git commit -m "feat(portal): schema-driven entity table"
```

---

## Task 11: Schema-driven edit form

**Files:**
- Create: `portal/src/pages/admin/data-maintenance/RecordEditForm.tsx`

- [ ] **Step 1: Implement**

Create `portal/src/pages/admin/data-maintenance/RecordEditForm.tsx`:

```tsx
import { useState } from 'react'
import type { EntitySchema } from '@/services/adminApi'
import { useAdminEdit } from '@/hooks/useAdmin'

interface Props {
  schema: EntitySchema
  record: Record<string, unknown>
  onClose: () => void
}

export function RecordEditForm({ schema, record, onClose }: Props) {
  const [form, setForm] = useState<Record<string, string>>(
    () => Object.fromEntries(schema.fields.map((f) => [f.name, record[f.name] == null ? '' : String(record[f.name])]))
  )
  const [error, setError] = useState('')
  const edit = useAdminEdit(schema.key)

  const editable = schema.fields.filter((f) => f.editable)

  const save = async () => {
    setError('')
    const patch = Object.fromEntries(editable.map((f) => [f.name, form[f.name] === '' ? null : form[f.name]]))
    try {
      await edit.mutateAsync({ id: String(record.id), patch })
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
        <h3 className="mb-4 text-base font-semibold">Edit {schema.label} · {String(record[schema.number_field] ?? '')}</h3>
        <div className="flex flex-col gap-3">
          {schema.fields.map((f) => (
            <div key={f.name} className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">{f.label}{!f.editable && ' (read-only)'}</label>
              <input
                value={form[f.name]}
                disabled={!f.editable}
                onChange={(e) => setForm((p) => ({ ...p, [f.name]: e.target.value }))}
                className="h-9 rounded-lg border border-neutral-300 px-3 text-sm disabled:bg-neutral-100 disabled:text-neutral-500"
              />
            </div>
          ))}
        </div>
        {error && <p className="mt-3 text-xs text-danger-600">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100">Cancel</button>
          <button onClick={save} disabled={edit.isPending}
            className="rounded-lg bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50">
            {edit.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run from `portal/`: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add portal/src/pages/admin/data-maintenance/RecordEditForm.tsx
git commit -m "feat(portal): schema-driven record edit form"
```

---

## Task 12: Delete preview + confirm dialog

**Files:**
- Create: `portal/src/pages/admin/data-maintenance/DeleteConfirm.tsx`

- [ ] **Step 1: Implement**

Create `portal/src/pages/admin/data-maintenance/DeleteConfirm.tsx`:

```tsx
import { useEffect, useState } from 'react'
import type { EntitySchema, CascadeSummary } from '@/services/adminApi'
import { adminApi } from '@/services/adminApi'
import { useAdminDelete } from '@/hooks/useAdmin'

interface Props {
  schema: EntitySchema
  record: Record<string, unknown>
  onClose: () => void
}

export function DeleteConfirm({ schema, record, onClose }: Props) {
  const [cascade, setCascade] = useState<CascadeSummary | null>(null)
  const [error, setError] = useState('')
  const del = useAdminDelete(schema.key)
  const id = String(record.id)

  useEffect(() => {
    adminApi.preview(schema.key, id).then(setCascade).catch((e) =>
      setError(e instanceof Error ? e.message : 'Preview failed'))
  }, [schema.key, id])

  const confirm = async () => {
    setError('')
    try { await del.mutateAsync(id); onClose() }
    catch (e) { setError(e instanceof Error ? e.message : 'Delete failed') }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h3 className="text-base font-semibold text-danger-700">
          Delete {schema.label} · {String(record[schema.number_field] ?? '')}
        </h3>
        <p className="mt-2 text-sm text-neutral-600">
          This cascades across the shared database and cannot be undone. The following will be removed:
        </p>
        <div className="mt-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-sm">
          {!cascade && !error && <p className="text-neutral-400">Computing impact…</p>}
          {cascade && (
            <ul className="space-y-1">
              {Object.entries(cascade).map(([table, n]) => (
                <li key={table} className="flex justify-between">
                  <span className="text-neutral-700">{table}</span>
                  <span className="font-semibold text-danger-700">{n}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        {error && <p className="mt-3 text-xs text-danger-600">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100">Cancel</button>
          <button onClick={confirm} disabled={del.isPending || !cascade}
            className="rounded-lg bg-danger-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-danger-700 disabled:opacity-50">
            {del.isPending ? 'Deleting…' : 'Delete permanently'}
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run from `portal/`: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add portal/src/pages/admin/data-maintenance/DeleteConfirm.tsx
git commit -m "feat(portal): cascade delete preview + confirm dialog"
```

---

## Task 13: Data Maintenance page + gated route/nav

**Files:**
- Create: `portal/src/pages/admin/DataMaintenance.tsx`
- Modify: portal router + nav (locate during this task)

- [ ] **Step 1: Implement the page**

Create `portal/src/pages/admin/DataMaintenance.tsx`:

```tsx
import { useState } from 'react'
import { useAdminEntities } from '@/hooks/useAdmin'
import type { EntitySchema } from '@/services/adminApi'
import { EntityTable } from './data-maintenance/EntityTable'
import { RecordEditForm } from './data-maintenance/RecordEditForm'
import { DeleteConfirm } from './data-maintenance/DeleteConfirm'

export default function DataMaintenance() {
  const { data: entities, isLoading } = useAdminEntities()
  const [activeKey, setActiveKey] = useState<string>('')
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null)
  const [deleting, setDeleting] = useState<Record<string, unknown> | null>(null)

  const active: EntitySchema | undefined = entities?.find((e) => e.key === (activeKey || entities[0]?.key))
  const systems = Array.from(new Set((entities ?? []).map((e) => e.system)))

  if (isLoading) return <p className="p-6 text-neutral-400">Loading…</p>

  return (
    <div className="flex flex-col gap-5 p-6">
      <div>
        <h1 className="text-lg font-semibold">Data Maintenance</h1>
        <p className="text-sm text-neutral-500">Browse, edit, and cascade-delete records. Every action is audited.</p>
      </div>

      {systems.map((sys) => (
        <div key={sys} className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase text-neutral-400">{sys}</span>
          {entities!.filter((e) => e.system === sys).map((e) => (
            <button key={e.key} onClick={() => setActiveKey(e.key)}
              className={`rounded-lg px-3 py-1.5 text-sm ${active?.key === e.key ? 'bg-primary-600 text-white' : 'bg-neutral-100 text-neutral-700 hover:bg-neutral-200'}`}>
              {e.label}
            </button>
          ))}
        </div>
      ))}

      {active && (
        <EntityTable schema={active} onEdit={setEditing} onDelete={setDeleting} />
      )}

      {active && editing && (
        <RecordEditForm schema={active} record={editing} onClose={() => setEditing(null)} />
      )}
      {active && deleting && (
        <DeleteConfirm schema={active} record={deleting} onClose={() => setDeleting(null)} />
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add a gated route + nav entry**

Locate the portal router (search `portal/src` for `createBrowserRouter` or `<Routes>` / `<Route`) and the nav/sidebar used by the existing admin area. Add:

```tsx
import DataMaintenance from '@/pages/admin/DataMaintenance'
// ...
<Route path="/admin/data-maintenance" element={<DataMaintenance />} />
```

Gate the nav link on the current user's `data_maintenance` permission (mirror how the portal already hides admin-only links — find the existing permission/role check used for the Admin area and reuse it; the permission key is `data_maintenance`). If the portal has no per-permission gating yet, gate on role `system_admin` as a minimum and leave a `// TODO: switch to data_maintenance permission` note.

- [ ] **Step 3: Typecheck**

Run from `portal/`: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors.

- [ ] **Step 4: Manual verification**

Start the stack (or portal + epms-api). As a `system_admin`:
1. Open `/admin/data-maintenance`.
2. Confirm EPMS entities (PR/PO/GR/Invoice/PA) appear; switch between them.
3. Search + paginate a populated entity.
4. Edit a record's editable field; confirm it persists and read-only fields are disabled.
5. Click Delete on a test record; confirm the cascade preview lists affected tables/counts; confirm deletion removes the record.
6. As a non-admin user, confirm the nav link is hidden and a direct API call returns 403.

- [ ] **Step 5: Commit**

```bash
git add portal/src/pages/admin/DataMaintenance.tsx portal/src/<router-and-nav-files>
git commit -m "feat(portal): Data Maintenance page with gated route and nav"
```

---

## Final verification

- [ ] Backend: from `epms-api/` run `python -m pytest tests/test_admin.py -v` — all pass.
- [ ] Backend: run the full suite `python -m pytest -q` — no regressions.
- [ ] Frontend: from `portal/` run `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` — clean.
- [ ] Migration: confirm `alembic upgrade head` applies `admin_audit_log` cleanly on a scratch DB.
- [ ] Manual end-to-end pass from Task 13 Step 4 completed.

## Self-review against spec (completed by plan author)

- **Metadata-driven framework** → Tasks 3–8 (schema dataclasses, registry, generic service, HTTP surface).
- **Full cascade incl. cross-"service"** → Task 5 cascade handlers; the shared-DB finding means cross-table = cross-service. Covered for EPMS tables + polymorphic tasks/approval_events. **Budget commitment release deferred** (documented under Scope notes) — this is the one spec line not implemented in Phase 1, by design.
- **Full-field edit** → Task 6 `edit_record` (editable-field enforcement + audit).
- **Preview + confirm** → Task 7 `delete_preview`, Task 8 `?preview=1`, Task 12 dialog.
- **Audit log** → Task 2 model; Tasks 6–7 writes; verified in tests.
- **Access: system_admin / data_maintenance** → Task 1 key; Task 8 `require_permission("data_maintenance")`.
- **Portal UI** → Tasks 9–13.
- **EPMS first, OA/VMS later** → entire plan is EPMS-scoped; registry pattern is reusable.
