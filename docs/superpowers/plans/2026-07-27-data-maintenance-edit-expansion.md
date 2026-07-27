# Data Maintenance — Expanded Edit for PR/PO/PA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the Data Maintenance admin Edit for PR/PO/PA (and GR/Invoice) to cover reference fields (Requester/Vendor/Cost Center), full line-item editing with header recompute, approval active-state editing, requester-change→approval-routing resync, and PO-number regeneration on vendor change.

**Architecture:** Additive extension of the existing metadata framework in `epms-api/app/admin/`. Reference fields and line items generalize into the framework (new `FieldSpec` attrs + `ChildSchema` + resolver registry); approval state, PO-number regeneration, and cross-service routing resync are bespoke per-entity/endpoint code. One new endpoint added to approval-api. Frontend Edit drawer becomes sectioned. No DB migration (all columns/tables already exist).

**Tech Stack:** FastAPI + SQLAlchemy async (Python), React + TanStack Query + Tailwind (Portal frontend), httpx for cross-service calls, pytest-asyncio.

**Reference spec:** `docs/superpowers/specs/2026-07-27-data-maintenance-edit-expansion-design.md`

**Test DB constraint:** epms-api tests hit the shared `epms_test` DB; run serially (never two pytest processes at once — see `feedback_uniops_test_db_concurrency`). Override `POSTGRES_*` to the local `uniops_postgres` container. approval-api tests need `PurchaseRequest` in its conftest `_ENGINE_TABLES`.

---

## File Structure

**Backend — epms-api (`epms-api/app/admin/`):**
- `fields.py` — MODIFY: add `ref_source`/`ref_name_field` to `FieldSpec`, `type="reference"`, add `ChildSchema` + `child` on `EntitySchema`.
- `resolvers.py` — CREATE: reference resolver registry (`users`/`vendors`/`cost_centers`).
- `recompute.py` — CREATE: per-entity header recompute callbacks (PR/PO/PA).
- `registry.py` — MODIFY: expand editable fields, add reference specs, attach `child` schemas + `recompute` + optional `on_reference_change` hook per entity.
- `service.py` — MODIFY: reference apply + name sync, `edit_child_collection` diff, invoke recompute, approval-state service fn.
- `po_number.py` — CREATE: PO-number regeneration + cascade rename.
- `app/api/v1/admin.py` — MODIFY: lookup endpoint, approval-state endpoint, accept `line_items` + `regenerate_po_number` in PATCH.
- `app/services/approval_client.py` — MODIFY: add `resync_document()`.

**Backend — approval-api:**
- `app/api/v1/routing.py` — MODIFY: add `POST /routing/resync-document`.

**Frontend — Portal (`portal/src/`):**
- `services/adminApi.ts` — MODIFY: extend types, add `lookup`, `editApprovalState`, pass regen flag.
- `pages/admin/data-maintenance/ReferencePicker.tsx` — CREATE.
- `pages/admin/data-maintenance/LineItemsEditor.tsx` — CREATE.
- `pages/admin/data-maintenance/ApprovalStatePanel.tsx` — CREATE.
- `pages/admin/data-maintenance/RecordEditForm.tsx` — MODIFY: sectioned layout, wire new components + PO regen checkbox.

**Tests:**
- `epms-api/tests/test_admin_edit_expansion.py` — CREATE (references, line items, recompute, approval state, PO regen).
- `approval-api/tests/test_resync_document_endpoint.py` — CREATE.

---

## Phase 1 — Reference fields (backend)

### Task 1: Extend FieldSpec + EntitySchema metadata

**Files:**
- Modify: `epms-api/app/admin/fields.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
# epms-api/tests/test_admin_edit_expansion.py
"""Tests for expanded Data Maintenance edit (references, line items, approval state)."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def test_fieldspec_reference_serialization():
    from app.admin.fields import FieldSpec

    f = FieldSpec("vendor_id", "reference", True, label="Vendor",
                  ref_source="vendors", ref_name_field="vendor_name")
    d = f.to_dict()
    assert d["type"] == "reference"
    assert d["ref_source"] == "vendors"
    assert d["ref_name_field"] == "vendor_name"


def test_entityschema_child_serialization():
    from app.admin.fields import EntitySchema, FieldSpec, ChildSchema
    from app.models.pr import PrLineItem

    child = ChildSchema(
        table_label="Line Items", model=PrLineItem, fk_field="pr_id",
        fields=[FieldSpec("description", "string", True), FieldSpec("qty", "decimal", True)],
    )
    schema = EntitySchema(
        key="pr", label="PR", number_field="number",
        list_columns=["number"], search_fields=["number"], order_by="created_at desc",
        fields=[FieldSpec("number", "string", False)], child=child,
    )
    d = schema.to_dict()
    assert d["child"]["table_label"] == "Line Items"
    assert d["child"]["fk_field"] == "pr_id"
    assert [f["name"] for f in d["child"]["fields"]] == ["description", "qty"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_fieldspec_reference_serialization -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'ref_source'`

- [ ] **Step 3: Implement metadata changes**

Replace `epms-api/app/admin/fields.py` `FieldType` line and `FieldSpec`, and add `ChildSchema` + `child` on `EntitySchema`:

```python
"""Schema dataclasses describing how a managed entity is rendered/edited."""
from __future__ import annotations

from dataclasses import dataclass, field

# adds "reference" — a FK the admin edits via a picker (id + denormalized name)
FieldType = str  # "string"|"number"|"decimal"|"bool"|"date"|"datetime"|"uuid"|"json"|"enum"|"reference"


@dataclass
class FieldSpec:
    name: str
    type: FieldType
    editable: bool
    label: str | None = None
    options: list[str] | None = None          # for enum types
    ref_source: str | None = None             # for reference: resolver key ("users"|"vendors"|"cost_centers")
    ref_name_field: str | None = None         # for reference: denormalized name column kept in sync

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "editable": self.editable,
            "label": self.label or self.name,
            "options": self.options,
            "ref_source": self.ref_source,
            "ref_name_field": self.ref_name_field,
        }


@dataclass
class ChildSchema:
    """A one-level child collection (line items) editable inline with the parent."""
    table_label: str
    model: type
    fk_field: str                              # child column pointing back at the parent id
    fields: list[FieldSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "table_label": self.table_label,
            "fk_field": self.fk_field,
            "fields": [f.to_dict() for f in self.fields],
        }

    def editable_field_names(self) -> set[str]:
        return {f.name for f in self.fields if f.editable}

    def field_type(self, name: str) -> str | None:
        for f in self.fields:
            if f.name == name:
                return f.type
        return None


@dataclass
class EntitySchema:
    key: str
    label: str
    number_field: str
    list_columns: list[str]
    search_fields: list[str]
    order_by: str
    fields: list[FieldSpec] = field(default_factory=list)
    allow_edit: bool = True                    # False = delete-only entity (no PATCH)
    child: ChildSchema | None = None           # line-item sub-collection (None = none)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "number_field": self.number_field,
            "list_columns": self.list_columns,
            "search_fields": self.search_fields,
            "order_by": self.order_by,
            "allow_edit": self.allow_edit,
            "fields": [f.to_dict() for f in self.fields],
            "child": self.child.to_dict() if self.child else None,
        }

    def editable_field_names(self) -> set[str]:
        return {f.name for f in self.fields if f.editable}

    def field_type(self, name: str) -> str | None:
        for f in self.fields:
            if f.name == name:
                return f.type
        return None

    def field_spec(self, name: str) -> FieldSpec | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py -k "serialization" -v`
Expected: PASS (2 tests). Also run existing `tests/test_admin.py::test_field_spec_serialization` — Expected: PASS (backward compatible; new keys added, existing keys unchanged).

> Note: `test_admin.py::test_field_spec_serialization` asserts an exact dict equality for a field. Update that existing assertion to include the two new keys:
> `{"name": "number", "type": "string", "editable": False, "label": "number", "options": None, "ref_source": None, "ref_name_field": None}`.

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/admin/fields.py epms-api/tests/test_admin_edit_expansion.py epms-api/tests/test_admin.py
git commit -m "feat(admin): reference FieldSpec + ChildSchema metadata"
```

---

### Task 2: Reference resolver registry

**Files:**
- Create: `epms-api/app/admin/resolvers.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_resolver_fetch_and_search_vendors(test_engine):
    from app.models.vendor import Vendor
    from app.admin.resolvers import get_resolver

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    vid = uuid.uuid4()
    async with factory() as db:
        db.add(Vendor(id=vid, code="V-ABC", name="Acme Supplies",
                      category="supplier", contact_name="A", contact_email="a@x.com"))
        await db.commit()

    resolver = get_resolver("vendors")
    async with factory() as db:
        got = await resolver.fetch_by_id(db, vid)
        assert got is not None and got.label == "Acme Supplies"
        hits = await resolver.search(db, "acme", limit=10)
        assert any(h.id == vid for h in hits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_resolver_fetch_and_search_vendors -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.admin.resolvers'`

- [ ] **Step 3: Implement the resolver registry**

```python
# epms-api/app/admin/resolvers.py
"""Reference-field resolvers: map a ref_source key to how we search/fetch the
target row and render its display label. Backed by the existing ORM models on the
shared DB."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_center import CostCenter
from app.models.user import User
from app.models.vendor import Vendor


@dataclass
class RefHit:
    id: uuid.UUID
    label: str


@dataclass
class Resolver:
    source: str
    fetch_by_id: Callable[[AsyncSession, uuid.UUID], Awaitable["RefHit | None"]]
    search: Callable[[AsyncSession, str, int], Awaitable[list["RefHit"]]]


def _user_label(u: User) -> str:
    return f"{u.full_name} <{u.email}>" if u.full_name else u.email


async def _user_by_id(db: AsyncSession, rid: uuid.UUID) -> RefHit | None:
    u = (await db.execute(select(User).where(User.id == rid))).scalar_one_or_none()
    return RefHit(u.id, _user_label(u)) if u else None


async def _user_search(db: AsyncSession, q: str, limit: int) -> list[RefHit]:
    stmt = select(User)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(User.full_name.ilike(term), User.email.ilike(term)))
    stmt = stmt.order_by(User.full_name.asc()).limit(limit)
    return [RefHit(u.id, _user_label(u)) for u in (await db.execute(stmt)).scalars().all()]


async def _vendor_by_id(db: AsyncSession, rid: uuid.UUID) -> RefHit | None:
    v = (await db.execute(select(Vendor).where(Vendor.id == rid))).scalar_one_or_none()
    return RefHit(v.id, v.name) if v else None


async def _vendor_search(db: AsyncSession, q: str, limit: int) -> list[RefHit]:
    stmt = select(Vendor)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(Vendor.name.ilike(term), Vendor.code.ilike(term)))
    stmt = stmt.order_by(Vendor.name.asc()).limit(limit)
    return [RefHit(v.id, v.name) for v in (await db.execute(stmt)).scalars().all()]


def _cc_label(c: CostCenter) -> str:
    return f"{c.code} — {c.name}"


async def _cc_by_id(db: AsyncSession, rid: uuid.UUID) -> RefHit | None:
    c = (await db.execute(select(CostCenter).where(CostCenter.id == rid))).scalar_one_or_none()
    return RefHit(c.id, _cc_label(c)) if c else None


async def _cc_search(db: AsyncSession, q: str, limit: int) -> list[RefHit]:
    stmt = select(CostCenter)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(CostCenter.name.ilike(term), CostCenter.code.ilike(term)))
    stmt = stmt.order_by(CostCenter.code.asc()).limit(limit)
    return [RefHit(c.id, _cc_label(c)) for c in (await db.execute(stmt)).scalars().all()]


_RESOLVERS: dict[str, Resolver] = {
    "users": Resolver("users", _user_by_id, _user_search),
    "vendors": Resolver("vendors", _vendor_by_id, _vendor_search),
    "cost_centers": Resolver("cost_centers", _cc_by_id, _cc_search),
}


def get_resolver(source: str) -> Resolver:
    r = _RESOLVERS.get(source)
    if r is None:
        raise ValueError(f"Unknown reference source '{source}'")
    return r


def resolver_sources() -> set[str]:
    return set(_RESOLVERS)
```

> Verify the `CostCenter` model import path: `grep -n "class CostCenter" epms-api/app/models/cost_center.py`. If the display columns differ (`code`/`name`), adjust `_cc_label`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_resolver_fetch_and_search_vendors -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/admin/resolvers.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): reference resolver registry (users/vendors/cost_centers)"
```

---

### Task 3: `edit_record` applies reference fields (id + name sync)

**Files:**
- Modify: `epms-api/app/admin/service.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_edit_reference_sets_id_and_syncs_name(test_engine):
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); new_vendor = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"c-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="Creator", role="requester"))
        db.add(Vendor(id=new_vendor, code="V-NEW", name="New Vendor Inc",
                      category="supplier", contact_name="N", contact_email="n@x.com"))
        await db.commit()
    pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-REF1", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), vendor_name="Old Vendor",
                               created_by=creator))
        await db.commit()

    async with factory() as db:
        await service.edit_record(db, "pr", pr_id, {"vendor_id": str(new_vendor)},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert str(pr.vendor_id) == str(new_vendor)
        assert pr.vendor_name == "New Vendor Inc"   # denormalized name synced


@pytest.mark.asyncio
async def test_edit_reference_unknown_id_rejected(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"c2-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C2", role="requester"))
        await db.commit()
    pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-REF2", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), created_by=creator))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ValueError, match="not found"):
            await service.edit_record(db, "pr", pr_id, {"vendor_id": str(uuid.uuid4())},
                                      actor_id=creator, actor_email="admin@x.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py -k "reference_sets_id or unknown_id" -v`
Expected: FAIL — `vendor_name` stays "Old Vendor" (name not synced) / no validation raised.

- [ ] **Step 3: Implement reference apply in `edit_record`**

In `epms-api/app/admin/service.py`, add an import and a helper, then branch on reference fields inside the patch loop. Add near the top:

```python
from app.admin.resolvers import get_resolver
```

Add a helper above `edit_record`:

```python
async def _apply_reference(db, spec, row, field, value):
    """Set an FK reference field + sync its denormalized name column. Returns the
    resolved label (for audit) or raises ValueError if the id is unknown."""
    if value in (None, ""):
        raise ValueError(f"Field '{field.name}' is a required reference and cannot be cleared")
    rid = uuid.UUID(str(value))
    hit = await get_resolver(field.ref_source).fetch_by_id(db, rid)
    if hit is None:
        raise ValueError(f"{field.ref_source} reference '{rid}' not found")
    setattr(row, field.name, rid)
    if field.ref_name_field:
        setattr(row, field.ref_name_field, hit.label)
    return hit.label
```

Replace the patch loop body in `edit_record` so reference fields route through `_apply_reference` and everything else keeps the existing `_coerce` path:

```python
    for key, value in patch.items():
        if key not in editable:
            raise ValueError(f"Field '{key}' is not editable")
        fspec = spec.schema.field_spec(key)
        if fspec is not None and fspec.type == "reference":
            await _apply_reference(db, spec, row, fspec, value)
        else:
            setattr(row, key, _coerce(spec.schema.field_type(key), value))
```

> The `before`/`after` audit snapshot already serializes every schema field, so a synced `vendor_name` change is captured automatically. Keep the existing audit block; it diffs `patch` keys, so also include the synced name field: after the loop, expand the audited key set to include any `ref_name_field` touched. Minimal change — replace the audit `before`/`after` comprehensions to snapshot the full row:
> `before={k: before[k] for k in before}` → keep full `before`; `after=after`. (Full-row before/after is acceptable and simpler; the AdminAuditLog columns are JSON.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py -k "reference_sets_id or unknown_id" -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/admin/service.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): apply reference fields with id+name sync and validation"
```

---

### Task 4: Expand editable fields + reference specs in registry

**Files:**
- Modify: `epms-api/app/admin/registry.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
def test_registry_pr_has_reference_and_expanded_fields():
    from app.admin.registry import REGISTRY

    schema = REGISTRY["pr"].schema
    names = {f.name for f in schema.fields}
    assert {"created_by", "vendor_id", "cost_center_id", "project_code",
            "delivery_address", "is_prepaid"} <= names
    created_by = schema.field_spec("created_by")
    assert created_by.type == "reference" and created_by.ref_source == "users"
    assert created_by.editable is True
    vendor = schema.field_spec("vendor_id")
    assert vendor.ref_source == "vendors" and vendor.ref_name_field == "vendor_name"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_registry_pr_has_reference_and_expanded_fields -v`
Expected: FAIL — `created_by` not in schema.

- [ ] **Step 3: Expand the schemas**

In `epms-api/app/admin/registry.py`, replace `_PR_SCHEMA`, `_PO_SCHEMA`, `_PA_SCHEMA` `fields=[...]` lists (keep the existing entries; add the new ones). PR:

```python
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("status", "enum", True, options=["draft", "submitted", "in_review", "approved", "returned", "rejected", "paid"]),
        FieldSpec("type", "number", True),
        FieldSpec("currency", "string", True),
        FieldSpec("amount", "decimal", True),
        FieldSpec("created_by", "reference", True, label="Requester", ref_source="users"),
        FieldSpec("vendor_id", "reference", True, label="Vendor", ref_source="vendors", ref_name_field="vendor_name"),
        FieldSpec("cost_center_id", "reference", True, label="Cost Center", ref_source="cost_centers", ref_name_field="cost_center_name"),
        FieldSpec("department_name", "string", True),
        FieldSpec("budget_code", "string", True),
        FieldSpec("project_code", "string", True),
        FieldSpec("delivery_address", "string", True),
        FieldSpec("is_prepaid", "bool", True),
        FieldSpec("over_budget", "bool", True),
        FieldSpec("over_budget_justification", "string", True),
        FieldSpec("notes", "string", True),
        FieldSpec("required_by", "date", True),
        FieldSpec("created_at", "datetime", False),
    ],
```

PO — add reference + open scalars (keep number/created_at read-only):

```python
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("status", "enum", True, options=["draft", "submitted", "in_review", "approved", "issued", "partially_received", "fully_received", "closed", "cancelled"]),
        FieldSpec("type", "number", True),
        FieldSpec("currency", "string", True),
        FieldSpec("subtotal", "decimal", True),
        FieldSpec("tax_rate", "decimal", True),
        FieldSpec("tax_code", "string", True),
        FieldSpec("tax_amount", "decimal", True),
        FieldSpec("total", "decimal", True),
        FieldSpec("created_by", "reference", True, label="Requester", ref_source="users"),
        FieldSpec("vendor_id", "reference", True, label="Vendor", ref_source="vendors", ref_name_field="vendor_name"),
        FieldSpec("budget_code", "string", True),
        FieldSpec("expected_delivery", "date", True),
        FieldSpec("delivery_address", "string", True),
        FieldSpec("is_prepaid", "bool", True),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
```

PA — add reference + charges + tax:

```python
    fields=[
        FieldSpec("pa_number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("status", "enum", True, options=["draft", "submitted", "in_review", "approved", "processed", "returned", "cancelled"]),
        FieldSpec("pa_type", "string", True),
        FieldSpec("currency", "string", True),
        FieldSpec("subtotal", "decimal", True),
        FieldSpec("tax_rate", "decimal", True),
        FieldSpec("tax_code", "string", True),
        FieldSpec("tax_amount", "decimal", True),
        FieldSpec("shipping_amount", "decimal", True),
        FieldSpec("other_charges", "decimal", True),
        FieldSpec("other_charges_note", "string", True),
        FieldSpec("payment_amount", "decimal", True),
        FieldSpec("created_by", "reference", True, label="PA Creator (AP Clerk)", ref_source="users"),
        FieldSpec("vendor_id", "reference", True, label="Vendor", ref_source="vendors", ref_name_field="vendor_name"),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
```

> `department_name` on PR stays a plain string (no `departments` FK on PR). `cost_center_name` is synced by the `cost_center_id` reference. Note PA's routing-relevant "Requester" is the source PR creator — handled in Task 11, not here; `created_by` here is the AP clerk (labelled as such).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_registry_pr_has_reference_and_expanded_fields -v`
Expected: PASS. Also run full `tests/test_admin.py` to confirm no regression — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/admin/registry.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): expand editable fields + reference specs for PR/PO/PA"
```

---

## Phase 2 — Line items (backend)

### Task 5: Per-entity header recompute callbacks

**Files:**
- Create: `epms-api/app/admin/recompute.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
def test_recompute_po_header():
    from decimal import Decimal
    from app.admin.recompute import recompute_header

    class _Row:
        tax_rate = Decimal("0.05")
    row = _Row()
    lines = [{"line_total": Decimal("100.00")}, {"line_total": Decimal("50.00")}]
    result = recompute_header("po", row, lines)
    assert result["subtotal"] == Decimal("150.00")
    assert result["tax_amount"] == Decimal("7.50")
    assert result["total"] == Decimal("157.50")


def test_recompute_pa_header_with_charges():
    from decimal import Decimal
    from app.admin.recompute import recompute_header

    class _Row:
        tax_rate = Decimal("0.10")
        shipping_amount = Decimal("20.00")
        other_charges = Decimal("5.00")
        prepayment_applied = None
    row = _Row()
    lines = [{"line_total": Decimal("200.00")}]
    result = recompute_header("pa", row, lines)
    assert result["subtotal"] == Decimal("200.00")
    assert result["tax_amount"] == Decimal("20.00")
    assert result["payment_amount"] == Decimal("245.00")  # 200 + 20 + 20 + 5 - 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py -k recompute -v`
Expected: FAIL — `ModuleNotFoundError: app.admin.recompute`

- [ ] **Step 3: Implement recompute**

```python
# epms-api/app/admin/recompute.py
"""Per-entity header recompute from line items. Mirrors the create-path totals in
crud/{pr,po,pa}.py. Returns a dict of header field -> new Decimal; the caller sets
them on the row."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def _q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _sum_lines(lines: list[dict]) -> Decimal:
    return _q(sum((Decimal(str(li["line_total"])) for li in lines), Decimal("0")))


def _pr(row, lines):
    return {"amount": _sum_lines(lines)}


def _po(row, lines):
    subtotal = _sum_lines(lines)
    rate = Decimal(str(getattr(row, "tax_rate", 0) or 0))
    tax = _q(subtotal * rate)
    return {"subtotal": subtotal, "tax_amount": tax, "total": _q(subtotal + tax)}


def _pa(row, lines):
    subtotal = _sum_lines(lines)
    rate = Decimal(str(getattr(row, "tax_rate", 0) or 0))
    tax = _q(subtotal * rate)
    shipping = Decimal(str(getattr(row, "shipping_amount", 0) or 0))
    other = Decimal(str(getattr(row, "other_charges", 0) or 0))
    prepay = Decimal(str(getattr(row, "prepayment_applied", 0) or 0))
    payment = _q(subtotal + tax + shipping + other - prepay)
    return {"subtotal": subtotal, "tax_amount": tax, "payment_amount": payment}


_RECOMPUTE = {"pr": _pr, "po": _po, "pa": _pa}


def recompute_header(entity: str, row, lines: list[dict]) -> dict:
    fn = _RECOMPUTE.get(entity)
    return fn(row, lines) if fn else {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py -k recompute -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/admin/recompute.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): per-entity header recompute from line items"
```

---

### Task 6: Attach `child` schemas to PR/PO/PA registry entries

**Files:**
- Modify: `epms-api/app/admin/registry.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
def test_registry_entities_have_child_line_items():
    from app.admin.registry import REGISTRY

    for key, fk in [("pr", "pr_id"), ("po", "po_id"), ("pa", "pa_id")]:
        child = REGISTRY[key].schema.child
        assert child is not None, f"{key} missing child schema"
        assert child.fk_field == fk
        names = {f.name for f in child.fields}
        assert {"description", "qty", "unit", "unit_price"} <= names
        # line_total is server-computed → read-only in the child schema
        lt = child.field_type("line_total")
        assert lt == "decimal"
        assert not any(f.name == "line_total" and f.editable for f in child.fields)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_registry_entities_have_child_line_items -v`
Expected: FAIL — `child is None`.

- [ ] **Step 3: Add child schemas + import models**

In `epms-api/app/admin/registry.py`, extend imports:

```python
from app.admin.fields import EntitySchema, FieldSpec, ChildSchema
from app.models.pr import PurchaseRequest, PrLineItem
from app.models.po import PurchaseOrder, PoLineItem
from app.models.pa import PaymentApplication, PaLineItem
```

Define child schemas before `REGISTRY`:

```python
_PR_CHILD = ChildSchema(
    table_label="Line Items", model=PrLineItem, fk_field="pr_id",
    fields=[
        FieldSpec("description", "string", True),
        FieldSpec("material_id", "string", True),
        FieldSpec("supplier_item_id", "string", True),
        FieldSpec("qty", "decimal", True),
        FieldSpec("unit", "string", True),
        FieldSpec("unit_price", "decimal", True),
        FieldSpec("line_total", "decimal", False),   # server-computed
        FieldSpec("notes", "string", True),
        FieldSpec("sort_order", "number", True),
    ],
)
_PO_CHILD = ChildSchema(
    table_label="Line Items", model=PoLineItem, fk_field="po_id",
    fields=[
        FieldSpec("description", "string", True),
        FieldSpec("material_id", "string", True),
        FieldSpec("supplier_item_id", "string", True),
        FieldSpec("qty", "decimal", True),
        FieldSpec("unit", "string", True),
        FieldSpec("unit_price", "decimal", True),
        FieldSpec("line_total", "decimal", False),
        FieldSpec("notes", "string", True),
        FieldSpec("sort_order", "number", True),
    ],
)
_PA_CHILD = ChildSchema(
    table_label="Line Items", model=PaLineItem, fk_field="pa_id",
    fields=[
        FieldSpec("description", "string", True),
        FieldSpec("qty", "decimal", True),
        FieldSpec("unit", "string", True),
        FieldSpec("unit_price", "decimal", True),
        FieldSpec("line_total", "decimal", False),
        FieldSpec("notes", "string", True),
        FieldSpec("sort_order", "number", True),
    ],
)
```

Set `child=` on each schema constructor: add `child=_PR_CHILD` to `_PR_SCHEMA`, `child=_PO_CHILD` to `_PO_SCHEMA`, `child=_PA_CHILD` to `_PA_SCHEMA`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_registry_entities_have_child_line_items -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/admin/registry.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): attach line-item child schemas to PR/PO/PA"
```

---

### Task 7: `edit_child_collection` — diff apply + recompute

**Files:**
- Modify: `epms-api/app/admin/service.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_edit_line_items_add_update_delete_and_recompute(test_engine):
    from app.models.user import User
    from app.models.po import PurchaseOrder, PoLineItem
    from app.models.vendor import Vendor
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); vid = uuid.uuid4(); po_id = uuid.uuid4(); keep_line = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"po-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        db.add(Vendor(id=vid, code="V-PO", name="V", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number="PO-LI1", title="t", type=1, status="draft",
                             currency="CAD", subtotal=Decimal("0"), tax_rate=Decimal("0.05"),
                             tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=vid, vendor_name="V", created_by=creator))
        await db.flush()
        db.add(PoLineItem(id=keep_line, po_id=po_id, description="old", qty=Decimal("1"),
                          unit="ea", unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0))
        await db.commit()

    # keep+update the existing line (qty 1->2), add a new line, (implicitly delete none here)
    line_items = [
        {"id": str(keep_line), "description": "updated", "qty": "2", "unit": "ea", "unit_price": "10"},
        {"description": "brand new", "qty": "3", "unit": "ea", "unit_price": "100"},
    ]
    async with factory() as db:
        await service.edit_record(db, "po", po_id, {"line_items": line_items},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()

    async with factory() as db:
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        lines = (await db.execute(select(PoLineItem).where(PoLineItem.po_id == po_id))).scalars().all()
        assert len(lines) == 2
        # 2*10 + 3*100 = 320 subtotal; tax 5% = 16.00; total 336.00
        assert po.subtotal == Decimal("320.00")
        assert po.tax_amount == Decimal("16.00")
        assert po.total == Decimal("336.00")
        assert {l.line_total for l in lines} == {Decimal("20.00"), Decimal("300.00")}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_edit_line_items_add_update_delete_and_recompute -v`
Expected: FAIL — `line_items` treated as an unknown field → ValueError.

- [ ] **Step 3: Implement child-collection editing in `edit_record`**

In `epms-api/app/admin/service.py`, add imports:

```python
from decimal import ROUND_HALF_UP
from sqlalchemy import delete as sa_delete
from app.admin.recompute import recompute_header
```

Add a helper:

```python
def _line_total(qty, unit_price) -> Decimal:
    return (Decimal(str(qty)) * Decimal(str(unit_price))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _apply_line_items(db, spec, row, items: list[dict]):
    """Diff the submitted line-item array against DB rows: update (has id), insert
    (no id), delete (existing id absent). line_total is recomputed server-side.
    Then recompute the header. Returns the list of resulting line dicts (for recompute)."""
    child = spec.schema.child
    if child is None:
        raise ValueError(f"'{spec.schema.key}' has no line items")
    Model = child.model
    editable = child.editable_field_names()
    existing = {li.id: li for li in (await db.execute(
        select(Model).where(getattr(Model, child.fk_field) == row.id))).scalars().all()}

    seen: set = set()
    for idx, item in enumerate(items):
        raw_id = item.get("id")
        qty = item.get("qty"); price = item.get("unit_price")
        if qty is None or price is None:
            raise ValueError("Each line item needs qty and unit_price")
        payload = {k: v for k, v in item.items() if k in editable and k != "line_total"}
        payload.setdefault("sort_order", idx)
        lt = _line_total(qty, price)
        if raw_id:
            li = existing.get(uuid.UUID(str(raw_id)))
            if li is None:
                raise ValueError(f"Line item '{raw_id}' does not belong to this record")
            for k, v in payload.items():
                setattr(li, k, _coerce(child.field_type(k), v))
            li.line_total = lt
            seen.add(li.id)
        else:
            coerced = {k: _coerce(child.field_type(k), v) for k, v in payload.items()}
            db.add(Model(**{child.fk_field: row.id}, line_total=lt, **coerced))

    # delete existing rows the payload dropped
    for lid, li in existing.items():
        if lid not in seen:
            await db.delete(li)

    await db.flush()
    lines = (await db.execute(
        select(Model).where(getattr(Model, child.fk_field) == row.id))).scalars().all()
    line_dicts = [{"line_total": l.line_total} for l in lines]
    for hk, hv in recompute_header(spec.schema.key, row, line_dicts).items():
        setattr(row, hk, hv)
```

In `edit_record`, split `line_items` out of the scalar patch before the field loop:

```python
    line_items = patch.pop("line_items", None)
    ...
    for key, value in patch.items():
        ...  # existing scalar / reference handling
    if line_items is not None:
        await _apply_line_items(db, spec, row, line_items)
```

> Put the `patch.pop` BEFORE `editable`/`before` are computed against `patch`, so `line_items` never hits the "not editable" guard. Include a `line_items` marker in the audit `after` (e.g. `after["_line_items_count"] = len(line_items)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_edit_line_items_add_update_delete_and_recompute -v`
Expected: PASS

Add a deletion-focused assertion test too:

```python
@pytest.mark.asyncio
async def test_edit_line_items_deletes_dropped_rows(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest, PrLineItem
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4(); l1 = uuid.uuid4(); l2 = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"pr-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-LI2", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), created_by=creator))
        await db.flush()
        db.add(PrLineItem(id=l1, pr_id=pr_id, description="a", qty=Decimal("1"), unit="ea",
                          unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0))
        db.add(PrLineItem(id=l2, pr_id=pr_id, description="b", qty=Decimal("1"), unit="ea",
                          unit_price=Decimal("20"), line_total=Decimal("20"), sort_order=1))
        await db.commit()
    async with factory() as db:
        await service.edit_record(db, "pr", pr_id,
                                  {"line_items": [{"id": str(l1), "description": "a", "qty": "1",
                                                   "unit": "ea", "unit_price": "10"}]},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    async with factory() as db:
        remaining = (await db.execute(select(PrLineItem).where(PrLineItem.pr_id == pr_id))).scalars().all()
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert {l.id for l in remaining} == {l1}
        assert pr.amount == Decimal("10.00")
```

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py -k line_items -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/admin/service.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): line-item diff editing with server recompute"
```

---

### Task 8: Serialize line items in `get_record`; accept them in the endpoint

**Files:**
- Modify: `epms-api/app/admin/service.py`, `epms-api/app/api/v1/admin.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_get_record_includes_line_items(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest, PrLineItem
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"g-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-G1", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("10"), created_by=creator))
        await db.flush()
        db.add(PrLineItem(id=uuid.uuid4(), pr_id=pr_id, description="a", qty=Decimal("1"),
                          unit="ea", unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0))
        await db.commit()
    async with factory() as db:
        rec = await service.get_record(db, "pr", pr_id)
        assert "line_items" in rec and len(rec["line_items"]) == 1
        assert rec["line_items"][0]["description"] == "a"
        assert rec["line_items"][0]["line_total"] == "10.00"
        assert "id" in rec["line_items"][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_get_record_includes_line_items -v`
Expected: FAIL — `line_items` not in the serialized record.

- [ ] **Step 3: Serialize child rows**

In `epms-api/app/admin/service.py`, add a child serializer and include it in `get_record` (only — not in `list_records`, to keep lists light):

```python
def _serialize_child(child, li) -> dict:
    out = {"id": str(getattr(li, "id"))}
    for f in child.fields:
        v = getattr(li, f.name, None)
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, uuid.UUID):
            v = str(v)
        out[f.name] = v
    return out
```

Change `get_record` to attach line items:

```python
async def get_record(db: AsyncSession, entity: str, record_id: uuid.UUID) -> dict | None:
    spec = _spec(entity)
    row = (await db.execute(select(spec.model).where(spec.model.id == record_id))).scalar_one_or_none()
    if row is None:
        return None
    rec = _serialize(spec, row)
    child = spec.schema.child
    if child is not None:
        lis = (await db.execute(
            select(child.model).where(getattr(child.model, child.fk_field) == row.id)
            .order_by(child.model.sort_order.asc()))).scalars().all()
        rec["line_items"] = [_serialize_child(child, li) for li in lis]
    return rec
```

The endpoint already forwards the whole PATCH body to `service.edit_record`, so `line_items` flows through with no endpoint change needed for editing. Confirm `admin.py`'s `edit_record` passes `patch` verbatim (it does).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_get_record_includes_line_items -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/admin/service.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): serialize line items in get_record"
```

---

## Phase 3 — Approval active state (backend)

### Task 9: Approval-state service + endpoint

**Files:**
- Modify: `epms-api/app/admin/service.py`, `epms-api/app/api/v1/admin.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_edit_approval_state_reassigns_open_tasks(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.models.task import Task
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4(); done_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"as-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-AS1", title="t", type=1, status="in_review",
                               currency="CAD", amount=Decimal("0"), created_by=creator,
                               approval_step_idx=1))
        await db.flush()
        db.add(Task(document_type="pr", document_id=pr_id, document_number="PR-AS1",
                    type="approve_pr", assigned_role="dept_manager", title="Approve", is_completed=False))
        db.add(Task(id=done_id, document_type="pr", document_id=pr_id, document_number="PR-AS1",
                    type="approve_pr", assigned_role="finance_bp", title="Done", is_completed=True))
        await db.commit()

    async with factory() as db:
        await service.edit_approval_state(db, "pr", pr_id,
                                          {"approval_step_idx": 2, "assigned_role": "finance_manager"},
                                          actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        tasks = (await db.execute(select(Task).where(Task.document_id == pr_id))).scalars().all()
        assert pr.approval_step_idx == 2
        open_t = [t for t in tasks if not t.is_completed]
        done_t = [t for t in tasks if t.is_completed]
        assert all(t.assigned_role == "finance_manager" for t in open_t)   # open reassigned
        assert done_t[0].assigned_role == "finance_bp"                     # completed untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_edit_approval_state_reassigns_open_tasks -v`
Expected: FAIL — `AttributeError: module 'app.admin.service' has no attribute 'edit_approval_state'`

- [ ] **Step 3: Implement `edit_approval_state`**

In `epms-api/app/admin/service.py`, add (import `Task` at top: `from app.models.task import Task`):

```python
async def edit_approval_state(db: AsyncSession, entity: str, record_id: uuid.UUID, patch: dict,
                              *, actor_id: uuid.UUID, actor_email: str) -> dict:
    """Manually correct a document's live approval position: its approval_step_idx
    and the assignment of its OPEN approve tasks. Does NOT re-run the engine, send
    notifications, or touch completed tasks / approval_events."""
    spec = _spec(entity)
    if entity not in ("pr", "po", "pa"):
        raise ValueError(f"'{entity}' has no approval state")
    row = await _load(db, spec, record_id)
    before = {"approval_step_idx": getattr(row, "approval_step_idx", None)}

    new_idx = patch.get("approval_step_idx")
    if new_idx is not None:
        row.approval_step_idx = int(new_idx)

    new_role = patch.get("assigned_role")
    new_user = patch.get("assigned_user_id")
    reassigned = 0
    if new_role is not None or new_user is not None:
        open_tasks = (await db.execute(select(Task).where(
            Task.document_id == record_id, Task.type.like("approve%"),
            Task.is_completed.is_(False)))).scalars().all()
        for t in open_tasks:
            if new_role is not None:
                t.assigned_role = new_role
            if new_user is not None:
                t.assigned_user_id = uuid.UUID(str(new_user)) if new_user else None
            reassigned += 1

    await db.flush()
    after = {"approval_step_idx": getattr(row, "approval_step_idx", None),
             "reassigned_open_tasks": reassigned,
             "assigned_role": new_role, "assigned_user_id": new_user}
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="edit_approval_state",
        system=spec.system, entity=entity, record_id=record_id,
        record_number=str(getattr(row, spec.schema.number_field, None)),
        before=before, after=after))
    await db.flush()
    return after
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_edit_approval_state_reassigns_open_tasks -v`
Expected: PASS

- [ ] **Step 5: Add the endpoint + commit**

In `epms-api/app/api/v1/admin.py`, add:

```python
@router.patch("/{entity}/{record_id}/approval-state")
async def edit_approval_state(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                              patch: dict = Body(...)):
    actor_id, email = _actor(user)
    try:
        result = await service.edit_approval_state(db, entity, record_id, patch,
                                                   actor_id=actor_id, actor_email=email)
        await db.commit()
        return result
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
```

```bash
git add epms-api/app/admin/service.py epms-api/app/api/v1/admin.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): approval active-state editor (step idx + open-task reassign)"
```

---

## Phase 4 — PO number regeneration on vendor change

### Task 10: PO-number regeneration + cascade rename

**Files:**
- Create: `epms-api/app/admin/po_number.py`
- Modify: `epms-api/app/admin/service.py`, `epms-api/app/api/v1/admin.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_po_vendor_change_regenerates_number_and_cascades(test_engine):
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.po import PurchaseOrder
    from app.models.pr import PurchaseRequest
    from app.models.task import Task
    from app.models.approval import ApprovalEvent
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); v_old = uuid.uuid4(); v_new = uuid.uuid4()
    pr_id = uuid.uuid4(); po_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"pn-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        db.add(Vendor(id=v_old, code="OLD", name="Old Co", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        db.add(Vendor(id=v_new, code="NEW", name="New Co", category="supplier",
                      contact_name="B", contact_email="b@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-PN1", title="t", type=1, status="approved",
                               currency="CAD", amount=Decimal("0"), created_by=creator,
                               po_id=po_id, po_number="PO-OLD-2501-01"))
        await db.flush()
        db.add(PurchaseOrder(id=po_id, number="PO-OLD-2501-01", title="t", type=1, status="approved",
                             currency="CAD", subtotal=Decimal("0"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=v_old, vendor_name="Old Co", pr_id=pr_id, created_by=creator))
        db.add(Task(document_type="po", document_id=po_id, document_number="PO-OLD-2501-01",
                    type="approve_po", assigned_role="finance_bp", title="Approve"))
        db.add(ApprovalEvent(document_type="po", document_id=po_id, document_number="PO-OLD-2501-01",
                             step_idx=0, action="submitted", actor_id=creator, actor_role="requester"))
        await db.commit()

    async with factory() as db:
        await service.edit_record(db, "po", po_id,
                                  {"vendor_id": str(v_new)},
                                  actor_id=creator, actor_email="admin@x.com",
                                  regenerate_po_number=True)
        await db.commit()

    async with factory() as db:
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        task = (await db.execute(select(Task).where(Task.document_id == po_id))).scalars().first()
        ev = (await db.execute(select(ApprovalEvent).where(ApprovalEvent.document_id == po_id))).scalars().first()
        assert po.number.startswith("PO-NEW-")          # regenerated with new vendor code
        assert po.number != "PO-OLD-2501-01"
        assert pr.po_number == po.number                # cascade → PR
        assert task.document_number == po.number        # cascade → tasks
        assert ev.document_number == po.number          # cascade → approval_events


@pytest.mark.asyncio
async def test_po_vendor_change_without_flag_keeps_number(test_engine):
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.po import PurchaseOrder
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); v_old = uuid.uuid4(); v_new = uuid.uuid4(); po_id = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"pk-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        db.add(Vendor(id=v_old, code="OLD2", name="Old2", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        db.add(Vendor(id=v_new, code="NEW2", name="New2", category="supplier",
                      contact_name="B", contact_email="b@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number="PO-OLD2-2501-01", title="t", type=1, status="draft",
                             currency="CAD", subtotal=Decimal("0"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=v_old, vendor_name="Old2", created_by=creator))
        await db.commit()
    async with factory() as db:
        await service.edit_record(db, "po", po_id, {"vendor_id": str(v_new)},
                                  actor_id=creator, actor_email="admin@x.com",
                                  regenerate_po_number=False)
        await db.commit()
    async with factory() as db:
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        assert po.number == "PO-OLD2-2501-01"           # unchanged
        assert po.vendor_name == "New2"                 # vendor still changed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py -k "regenerates_number or keeps_number" -v`
Expected: FAIL — `edit_record() got an unexpected keyword argument 'regenerate_po_number'`

- [ ] **Step 3: Implement regeneration + cascade**

Create `epms-api/app/admin/po_number.py`:

```python
"""PO number regeneration + cascade rename across denormalized copies.

PO number format: PO-{vendor_code}-{YYMM}-{seq:02d} (see crud/po._next_number).
Changing the vendor makes the vendor_code segment stale; regeneration mints a new
number under the new vendor's code and renames every denormalized copy joined by
po_id (reliable). finance ap_invoices has no po_id FK → best-effort string match."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.approval import ApprovalEvent


async def _next_number(db: AsyncSession, vendor_code: str) -> str:
    ym = datetime.now(timezone.utc).strftime("%y%m")
    prefix = f"PO-{vendor_code}-{ym}-"
    count = (await db.execute(
        select(func.count()).where(PurchaseOrder.number.like(f"{prefix}%")))).scalar_one()
    return f"{prefix}{count + 1:02d}"


async def regenerate_and_cascade(db: AsyncSession, po, vendor_code: str) -> dict[str, int | str]:
    """Mint a new number for `po` under vendor_code, cascade-rename all denormalized
    copies. Caller commits. Returns a summary for the audit cascade_summary."""
    old = po.number
    new = await _next_number(db, vendor_code)
    summary: dict[str, int | str] = {"old_number": old, "new_number": new}

    po.number = new

    async def _bump(model, col):
        res = await db.execute(update(model).where(model.po_id == po.id).values(**{col: new}))
        return res.rowcount or 0

    summary["purchase_requests"] = (await db.execute(
        update(PurchaseRequest).where(PurchaseRequest.po_id == po.id)
        .values(po_number=new))).rowcount or 0
    summary["payment_applications"] = await _bump(PaymentApplication, "po_number")
    summary["goods_receipts"] = await _bump(GoodsReceipt, "po_number")
    summary["invoices"] = await _bump(Invoice, "po_number")
    summary["tasks"] = (await db.execute(
        update(Task).where(Task.document_id == po.id, Task.document_type == "po")
        .values(document_number=new))).rowcount or 0
    summary["approval_events"] = (await db.execute(
        update(ApprovalEvent).where(ApprovalEvent.document_id == po.id,
                                    ApprovalEvent.document_type == "po")
        .values(document_number=new))).rowcount or 0

    # finance ap_invoices: no po_id FK on that mirror → best-effort string match.
    # Guard on table existence (absent in the epms test DB).
    exists = (await db.execute(text("SELECT to_regclass('ap_invoices')"))).scalar_one()
    if exists is not None:
        res = await db.execute(
            text("UPDATE ap_invoices SET po_number = :new WHERE po_number = :old"),
            {"new": new, "old": old})
        summary["ap_invoices_by_string"] = res.rowcount or 0

    return summary
```

In `epms-api/app/admin/service.py`, extend `edit_record`'s signature and, after the field loop, run regeneration when a PO's vendor actually changed and the flag is set. Add import:

```python
from app.admin.po_number import regenerate_and_cascade
from app.admin.resolvers import get_resolver  # already added in Task 3
from app.models.vendor import Vendor
```

Change signature:

```python
async def edit_record(db, entity, record_id, patch, *, actor_id, actor_email,
                      regenerate_po_number: bool = False):
```

Capture the old vendor before applying, and after applying references, regenerate if needed. Insert near the top of `edit_record` (after `row` is loaded):

```python
    old_vendor_id = getattr(row, "vendor_id", None)
```

After the field loop and line-item apply, before building the audit `after`:

```python
    cascade = None
    if entity == "po" and regenerate_po_number:
        new_vendor_id = getattr(row, "vendor_id", None)
        if new_vendor_id is not None and str(new_vendor_id) != str(old_vendor_id):
            vendor = (await db.execute(
                select(Vendor).where(Vendor.id == new_vendor_id))).scalar_one_or_none()
            if vendor is None:
                raise ValueError("New vendor not found for PO number regeneration")
            cascade = await regenerate_and_cascade(db, row, vendor.code)
```

Attach `cascade` to the audit row: pass `cascade_summary=cascade` to the `AdminAuditLog(...)` constructor in `edit_record` (the column already exists).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py -k "regenerates_number or keeps_number" -v`
Expected: PASS (2)

- [ ] **Step 5: Wire the endpoint flag + commit**

In `epms-api/app/api/v1/admin.py`, read the flag from a query param (keeps the PATCH body as the raw patch):

```python
@router.patch("/{entity}/{record_id}")
async def edit_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                      patch: dict = Body(...),
                      regenerate_po_number: int = Query(0)):
    actor_id, email = _actor(user)
    try:
        result = await service.edit_record(db, entity, record_id, patch, actor_id=actor_id,
                                            actor_email=email,
                                            regenerate_po_number=bool(regenerate_po_number))
        await db.commit()
        return result
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
```

```bash
git add epms-api/app/admin/po_number.py epms-api/app/admin/service.py epms-api/app/api/v1/admin.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): regenerate PO number + cascade rename on vendor change"
```

---

## Phase 5 — Requester change → approval routing resync (cross-service)

### Task 11: approval-api single-document resync endpoint

**Files:**
- Modify: `approval-api/app/api/v1/routing.py`
- Test: `approval-api/tests/test_resync_document_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# approval-api/tests/test_resync_document_endpoint.py
"""The single-document resync endpoint delegates to _resync_document."""
import uuid

import pytest


@pytest.mark.asyncio
async def test_resync_document_calls_engine(monkeypatch):
    from app.api.v1 import routing

    called = {}

    async def _fake_resync(db, doc_type, doc_id):
        called["args"] = (doc_type, str(doc_id))
        return {"doc_type": doc_type, "number": "PR-X", "actions": ["reissue"], "final_step": 1}

    monkeypatch.setattr(routing, "_resync_document", _fake_resync, raising=False)

    class _DB:
        async def commit(self): called["committed"] = True

    did = uuid.uuid4()
    result = await routing.resync_document(
        body={"doc_type": "pr", "doc_id": str(did)},
        db=_DB(), user={"role": "system_admin", "sub": str(uuid.uuid4())})
    assert called["args"] == ("pr", str(did))
    assert called["committed"] is True
    assert result["resynced"]["actions"] == ["reissue"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd approval-api && python -m pytest tests/test_resync_document_endpoint.py -v`
Expected: FAIL — `AttributeError: module 'app.api.v1.routing' has no attribute 'resync_document'`

- [ ] **Step 3: Implement the endpoint**

In `approval-api/app/api/v1/routing.py`, extend the engine import and add the endpoint:

```python
from app.crud.engine import resync_inflight_approvals, _resync_document
```

```python
@router.post("/resync-document")
async def resync_document(body: dict, db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    """Realign ONE in-flight document to current routing config (single-doc variant of
    resync-inflight). Used after an admin corrects a document's Requester so the
    department-derived approvers follow. system_admin only."""
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    doc_type = body.get("doc_type")
    doc_id = body.get("doc_id")
    if not doc_type or not doc_id:
        raise HTTPException(status_code=422, detail="doc_type and doc_id required")
    try:
        summary = await _resync_document(db, doc_type, uuid.UUID(str(doc_id)))
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}")
    await db.commit()
    return {"resynced": summary}
```

Add `import uuid` at the top of `routing.py` if not present.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd approval-api && python -m pytest tests/test_resync_document_endpoint.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add approval-api/app/api/v1/routing.py approval-api/tests/test_resync_document_endpoint.py
git commit -m "feat(approval): single-document resync endpoint for admin requester fixes"
```

---

### Task 12: epms-api triggers resync on Requester change

**Files:**
- Modify: `epms-api/app/services/approval_client.py`, `epms-api/app/admin/service.py`, `epms-api/app/api/v1/admin.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test** (mock the HTTP call; assert it fires on `created_by` change for PR)

```python
@pytest.mark.asyncio
async def test_requester_change_triggers_routing_resync(test_engine, monkeypatch):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.admin import service
    from app.services import approval_client

    calls = []

    async def _fake_resync(doc_type, doc_id, bearer_token):
        calls.append((doc_type, str(doc_id)))
        return {"resynced": {"actions": ["reissue"]}}

    monkeypatch.setattr(approval_client, "resync_document", _fake_resync)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    old_creator = uuid.uuid4(); new_creator = uuid.uuid4(); pr_id = uuid.uuid4()
    async with factory() as db:
        for u, name in [(old_creator, "Old"), (new_creator, "New")]:
            db.add(User(id=u, email=f"{u.hex[:6]}@x.com", hashed_password="x",
                        full_name=name, role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-RS1", title="t", type=1, status="in_review",
                               currency="CAD", amount=Decimal("0"), created_by=old_creator))
        await db.commit()

    async with factory() as db:
        result = await service.edit_record(db, "pr", pr_id, {"created_by": str(new_creator)},
                                           actor_id=old_creator, actor_email="admin@x.com",
                                           bearer_token="tok")
        await db.commit()
    assert ("pr", str(pr_id)) in calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_requester_change_triggers_routing_resync -v`
Expected: FAIL — `edit_record() got an unexpected keyword argument 'bearer_token'` / no `resync_document`.

- [ ] **Step 3: Implement client fn + trigger**

Add to `epms-api/app/services/approval_client.py`:

```python
async def resync_document(doc_type: str, doc_id: str, bearer_token: str) -> dict:
    """Call POST /routing/resync-document on the Approval Engine to realign this
    document to current routing (used after an admin changes its Requester).
    Raises RuntimeError on unreachable / error so the caller can surface a warning."""
    url = f"{settings.APPROVAL_ENGINE_URL}/routing/resync-document"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json={"doc_type": doc_type, "doc_id": doc_id},
                                 headers={"Authorization": f"Bearer {bearer_token}"})
    if not resp.is_success:
        raise RuntimeError(f"resync-document {resp.status_code}: {resp.text[:200]}")
    return resp.json()
```

In `epms-api/app/admin/service.py` `edit_record`, extend signature with `bearer_token: str | None = None` and, after commit-relevant mutations but returning the flag to the endpoint (which commits), record whether a routing-relevant requester changed. Because the resync must run AFTER the DB commit (approval-api reads the shared DB), return a signal to the endpoint rather than calling within the transaction:

```python
# in edit_record signature:
async def edit_record(db, entity, record_id, patch, *, actor_id, actor_email,
                      regenerate_po_number: bool = False, bearer_token: str | None = None):
```

Track requester change near where references apply:

```python
    routing_requester_changed = False
    ...
    # inside the reference branch, after _apply_reference for created_by on a PR:
    if entity == "pr" and fspec.name == "created_by":
        routing_requester_changed = True
```

Return it in the result payload:

```python
    after["_routing_requester_changed"] = routing_requester_changed
    return after
```

In `epms-api/app/api/v1/admin.py` `edit_record`, after `await db.commit()`, fire the resync (post-commit, best-effort, surfaced as a warning — never fails the edit):

```python
        result = await service.edit_record(db, entity, record_id, patch, actor_id=actor_id,
                                            actor_email=email,
                                            regenerate_po_number=bool(regenerate_po_number),
                                            bearer_token=user.get("_token"))
        await db.commit()
        if result.pop("_routing_requester_changed", False):
            from app.services import approval_client
            try:
                await approval_client.resync_document(entity, str(record_id),
                                                      bearer_token=_bearer(user))
                result["routing_resync"] = "ok"
            except Exception as e:
                result["routing_resync"] = f"failed: {e}"
        return result
```

> `_bearer(user)`: the admin endpoints authenticate via `require_permission`; confirm how the raw token reaches the handler. If `require_permission` doesn't expose the token, add `request: Request` to the handler and read `request.headers["authorization"].removeprefix("Bearer ").strip()`. Implement `_bearer` accordingly and reuse it. For PA, Task 13 replaces this trigger with the source-PR variant.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_requester_change_triggers_routing_resync -v`
Expected: PASS

> The unit test targets `service.edit_record` calling nothing itself — adjust: the trigger lives in the endpoint, so the test should assert `result["_routing_requester_changed"] is True` at the service layer, and a separate endpoint-level test (using FastAPI `TestClient` with `approval_client.resync_document` monkeypatched) asserts the call fires. Split into `test_requester_change_flags_resync` (service) + `test_edit_endpoint_fires_resync` (endpoint). Rewrite the Step-1 test into these two before implementing if the engineer prefers strict layering.

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/services/approval_client.py epms-api/app/admin/service.py epms-api/app/api/v1/admin.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): trigger approval routing resync on PR requester change"
```

---

### Task 13: PA "Requester" edits the source PR creator (B-1)

**Files:**
- Modify: `epms-api/app/admin/service.py`
- Test: `epms-api/tests/test_admin_edit_expansion.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_pa_source_requester_edits_source_pr_creator(test_engine, monkeypatch):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.models.po import PurchaseOrder
    from app.models.pa import PaymentApplication
    from app.models.vendor import Vendor
    from app.admin import service
    from app.services import approval_client

    calls = []
    async def _fake(doc_type, doc_id, bearer_token):
        calls.append((doc_type, str(doc_id))); return {}
    monkeypatch.setattr(approval_client, "resync_document", _fake)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    old_c = uuid.uuid4(); new_c = uuid.uuid4(); vid = uuid.uuid4()
    pr_id = uuid.uuid4(); po_id = uuid.uuid4(); pa_id = uuid.uuid4()
    async with factory() as db:
        for u in (old_c, new_c):
            db.add(User(id=u, email=f"{u.hex[:6]}@x.com", hashed_password="x",
                        full_name="U", role="requester"))
        db.add(Vendor(id=vid, code="V", name="V", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-PA1", title="t", type=1, status="approved",
                               currency="CAD", amount=Decimal("0"), created_by=old_c))
        await db.flush()
        db.add(PurchaseOrder(id=po_id, number="PO-PA1", title="t", type=1, status="approved",
                             currency="CAD", subtotal=Decimal("0"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=vid, vendor_name="V", pr_id=pr_id, created_by=old_c))
        await db.flush()
        db.add(PaymentApplication(id=pa_id, pa_number="PA-1", title="t", po_id=po_id, po_number="PO-PA1",
                                  vendor_id=vid, vendor_name="V", pa_type="regular",
                                  subtotal=Decimal("0"), tax_amount=Decimal("0"),
                                  payment_amount=Decimal("0"), currency="CAD",
                                  status="in_review", created_by=old_c))
        await db.commit()

    async with factory() as db:
        result = await service.edit_record(db, "pa", pa_id, {"source_requester_id": str(new_c)},
                                           actor_id=old_c, actor_email="admin@x.com", bearer_token="t")
        await db.commit()
    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert str(pr.created_by) == str(new_c)         # source PR creator updated
    # resync fires for the PA and/or PR chain
    assert result.get("_routing_requester_changed") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_pa_source_requester_edits_source_pr_creator -v`
Expected: FAIL — `source_requester_id` is not an editable field.

- [ ] **Step 3: Implement the virtual `source_requester_id` field for PA**

`source_requester_id` is a **virtual** field (not a column). Handle it explicitly in `edit_record` before the normal loop, for `entity == "pa"`:

```python
    if entity == "pa":
        src_req = patch.pop("source_requester_id", None)
        if src_req not in (None, ""):
            po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == row.po_id))).scalar_one_or_none()
            if po is None or po.pr_id is None:
                raise ValueError("This PA has no linked source PR; source Requester cannot be changed")
            src_pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == po.pr_id))).scalar_one()
            hit = await get_resolver("users").fetch_by_id(db, uuid.UUID(str(src_req)))
            if hit is None:
                raise ValueError(f"users reference '{src_req}' not found")
            src_pr.created_by = hit.id
            routing_requester_changed = True
```

Add `source_requester_id` to the PA schema as a `reference` field (users) so the frontend renders a picker and it passes the editable-name guard. But since it is virtual (no column), exclude it from `_serialize`/`_coerce`: mark it and skip in `_serialize`. Simplest: add it to the schema `fields` with a sentinel and special-case it in `_serialize` (return the resolved source PR creator id) — for the plan, add:

```python
FieldSpec("source_requester_id", "reference", True, label="Source Requester (PR creator)", ref_source="users"),
```

In `_serialize`, skip virtual fields:

```python
    for f in spec.schema.fields:
        if f.name == "source_requester_id":
            continue   # virtual — resolved on demand, not a column
        ...
```

Ensure the `patch.pop("source_requester_id", ...)` runs before the editable-field loop so it never hits the scalar path.

Import `PurchaseOrder`, `PurchaseRequest` at the top of `service.py` if not already.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_admin_edit_expansion.py::test_pa_source_requester_edits_source_pr_creator -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/admin/service.py epms-api/app/admin/registry.py epms-api/tests/test_admin_edit_expansion.py
git commit -m "feat(admin): PA source-Requester edits linked PR creator (B-1) + resync"
```

- [ ] **Step 6: Run the full backend suites (serially)**

Run: `cd epms-api && python -m pytest tests/test_admin.py tests/test_admin_edit_expansion.py -v`
Then: `cd approval-api && python -m pytest tests/test_resync_document_endpoint.py -v`
Expected: all PASS. (Use the local `uniops_postgres` container per env override; never run two epms suites concurrently.)

---

## Phase 6 — Frontend

### Task 14: Extend adminApi types + lookup + approvalState + regen flag

**Files:**
- Modify: `portal/src/services/adminApi.ts`
- Test: manual (typecheck)

- [ ] **Step 1: Extend types and methods**

In `portal/src/services/adminApi.ts`, extend `FieldSpec`, `EntitySchema`, and add methods:

```typescript
export interface FieldSpec {
  name: string
  type: 'string' | 'number' | 'decimal' | 'bool' | 'date' | 'datetime' | 'uuid' | 'json' | 'enum' | 'reference'
  editable: boolean
  label: string
  options: string[] | null
  ref_source?: string | null
  ref_name_field?: string | null
}

export interface ChildSchema {
  table_label: string
  fk_field: string
  fields: FieldSpec[]
}

export interface EntitySchema {
  key: string
  label: string
  system: string
  number_field: string
  list_columns: string[]
  search_fields: string[]
  order_by: string
  allow_edit?: boolean
  fields: FieldSpec[]
  child?: ChildSchema | null
}

export interface RefHit { id: string; label: string }
```

Add to the `adminApi` object:

```typescript
  lookup: (system: string, source: string, q: string) =>
    request<RefHit[]>(system, 'GET', `/admin/lookup/${source}${qs({ q, limit: 20 })}`),
  editWithOptions: (system: string, entity: string, id: string,
                    patch: Record<string, unknown>, opts?: { regeneratePoNumber?: boolean }) =>
    request<Record<string, unknown>>(system, 'PATCH',
      `/admin/${entity}/${id}${qs({ regenerate_po_number: opts?.regeneratePoNumber ? 1 : 0 })}`, patch),
  editApprovalState: (system: string, entity: string, id: string, patch: Record<string, unknown>) =>
    request<Record<string, unknown>>(system, 'PATCH', `/admin/${entity}/${id}/approval-state`, patch),
```

- [ ] **Step 2: Add the lookup endpoint on the backend**

In `epms-api/app/api/v1/admin.py`, add (imports `from app.admin.resolvers import get_resolver, resolver_sources`):

```python
@router.get("/lookup/{source}")
async def lookup(source: str, db: SessionDep, user: AdminUser,
                 q: str = Query(""), limit: int = Query(20, le=50)):
    if source not in resolver_sources():
        raise HTTPException(404, f"Unknown reference source '{source}'")
    hits = await get_resolver(source).search(db, q, limit)
    return [{"id": str(h.id), "label": h.label} for h in hits]
```

> Route ordering: define `/lookup/{source}` BEFORE the `/{entity}` catch-all in the router, or FastAPI will match `lookup` as an entity. Place it directly under `list_entities`.

- [ ] **Step 3: Typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors (Portal is TS6.0.3 per `reference_uniops_frontend_tsc6`).

- [ ] **Step 4: Commit**

```bash
git add portal/src/services/adminApi.ts epms-api/app/api/v1/admin.py
git commit -m "feat(admin-ui): adminApi types + lookup/approval-state/regen; lookup endpoint"
```

---

### Task 15: ReferencePicker component

**Files:**
- Create: `portal/src/pages/admin/data-maintenance/ReferencePicker.tsx`

- [ ] **Step 1: Implement the picker** (typeahead → `adminApi.lookup`, overlay via `createPortal`, per `feedback_uniops_overlay_dropdown_portal`)

```tsx
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { adminApi, type RefHit } from '@/services/adminApi'

interface Props {
  system: string
  source: string
  valueLabel: string           // current display (e.g. existing vendor_name / requester)
  onPick: (hit: RefHit) => void
}

export function ReferencePicker({ system, source, valueLabel, onPick }: Props) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<RefHit[]>([])
  const [rect, setRect] = useState<DOMRect | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const t = setTimeout(async () => {
      try { setHits(await adminApi.lookup(system, source, q)) } catch { setHits([]) }
    }, 200)
    return () => clearTimeout(t)
  }, [q, open, system, source])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (triggerRef.current?.contains(e.target as Node)) return
      if (popRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const toggle = () => {
    setRect(triggerRef.current?.getBoundingClientRect() ?? null)
    setOpen((o) => !o)
  }

  return (
    <>
      <button ref={triggerRef} type="button" onClick={toggle}
        className="h-9 truncate rounded-lg border border-neutral-300 px-3 text-left text-sm hover:bg-neutral-50">
        {valueLabel || <span className="text-neutral-400">Select…</span>}
      </button>
      {open && rect && createPortal(
        <div ref={popRef} style={{ position: 'fixed', top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 260) }}
          className="z-[60] rounded-lg border border-neutral-200 bg-white shadow-lg">
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…"
            className="m-2 h-8 w-[calc(100%-1rem)] rounded border border-neutral-300 px-2 text-sm" />
          <ul className="max-h-64 overflow-y-auto pb-1">
            {hits.map((h) => (
              <li key={h.id}>
                <button type="button" onClick={() => { onPick(h); setOpen(false) }}
                  className="block w-full truncate px-3 py-1.5 text-left text-sm hover:bg-primary-50">{h.label}</button>
              </li>
            ))}
            {hits.length === 0 && <li className="px-3 py-2 text-xs text-neutral-400">No matches</li>}
          </ul>
        </div>, document.body)}
    </>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add portal/src/pages/admin/data-maintenance/ReferencePicker.tsx
git commit -m "feat(admin-ui): ReferencePicker typeahead component"
```

---

### Task 16: LineItemsEditor component

**Files:**
- Create: `portal/src/pages/admin/data-maintenance/LineItemsEditor.tsx`

- [ ] **Step 1: Implement the editor** (add/delete rows, inline edit, live total preview)

```tsx
import { useMemo } from 'react'
import type { ChildSchema } from '@/services/adminApi'

export interface LineRow { id?: string; [k: string]: unknown }

interface Props {
  child: ChildSchema
  rows: LineRow[]
  onChange: (rows: LineRow[]) => void
}

const dec = (v: unknown) => { const n = Number(v); return Number.isFinite(n) ? n : 0 }

export function LineItemsEditor({ child, rows, onChange }: Props) {
  const editable = child.fields.filter((f) => f.editable)

  const set = (i: number, name: string, value: string) => {
    const next = rows.map((r, j) => (j === i ? { ...r, [name]: value } : r))
    onChange(next)
  }
  const addRow = () => onChange([...rows, Object.fromEntries(editable.map((f) => [f.name, ''])) as LineRow])
  const delRow = (i: number) => onChange(rows.filter((_, j) => j !== i))

  const subtotal = useMemo(
    () => rows.reduce((s, r) => s + dec(r.qty) * dec(r.unit_price), 0), [rows])

  return (
    <div className="rounded-lg border border-neutral-200">
      <div className="flex items-center justify-between border-b border-neutral-100 px-3 py-2">
        <span className="text-sm font-medium">{child.table_label}</span>
        <button type="button" onClick={addRow}
          className="rounded bg-primary-600 px-2 py-1 text-xs font-semibold text-white hover:bg-primary-700">+ Add row</button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-neutral-500">
              {editable.map((f) => <th key={f.name} className="px-2 py-1">{f.label}</th>)}
              <th className="px-2 py-1">Line Total</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.id ?? `new-${i}`} className="border-t border-neutral-100">
                {editable.map((f) => (
                  <td key={f.name} className="px-1 py-1">
                    <input value={r[f.name] == null ? '' : String(r[f.name])}
                      onChange={(e) => set(i, f.name, e.target.value)}
                      className="h-8 w-full min-w-[6rem] rounded border border-neutral-300 px-2" />
                  </td>
                ))}
                <td className="px-2 py-1 tabular-nums">{(dec(r.qty) * dec(r.unit_price)).toFixed(2)}</td>
                <td className="px-1 py-1">
                  <button type="button" onClick={() => delRow(i)}
                    className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50">Delete</button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={editable.length + 2} className="px-3 py-3 text-center text-xs text-neutral-400">No line items</td></tr>
            )}
          </tbody>
          <tfoot>
            <tr className="border-t border-neutral-200 font-medium">
              <td colSpan={editable.length} className="px-2 py-2 text-right">Subtotal (preview)</td>
              <td className="px-2 py-2 tabular-nums">{subtotal.toFixed(2)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="px-3 py-1 text-[11px] text-neutral-400">Server recomputes line totals, subtotal, tax and total on save.</p>
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add portal/src/pages/admin/data-maintenance/LineItemsEditor.tsx
git commit -m "feat(admin-ui): LineItemsEditor with add/delete + live subtotal"
```

---

### Task 17: ApprovalStatePanel component

**Files:**
- Create: `portal/src/pages/admin/data-maintenance/ApprovalStatePanel.tsx`

- [ ] **Step 1: Implement the panel** (edit step idx + target role; calls `adminApi.editApprovalState`)

```tsx
import { useState } from 'react'
import { adminApi } from '@/services/adminApi'

interface Props {
  system: string
  entity: string
  recordId: string
  currentStep: number | null
}

const ROLES = ['dept_manager', 'finance_bp', 'finance_manager', 'gm', 'opm', 'director',
                'supervisor', 'ap_clerk', 'quality_manager']

export function ApprovalStatePanel({ system, entity, recordId, currentStep }: Props) {
  const [step, setStep] = useState(currentStep == null ? '' : String(currentStep))
  const [role, setRole] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const apply = async () => {
    setBusy(true); setMsg('')
    try {
      const patch: Record<string, unknown> = {}
      if (step !== '') patch.approval_step_idx = Number(step)
      if (role !== '') patch.assigned_role = role
      const res = await adminApi.editApprovalState(system, entity, recordId, patch)
      setMsg(`Updated. Reassigned ${res.reassigned_open_tasks ?? 0} open task(s).`)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Failed')
    } finally { setBusy(false) }
  }

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/40 p-3">
      <p className="mb-2 text-sm font-medium">Approval State (manual override)</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-neutral-600">Current step
          <input value={step} onChange={(e) => setStep(e.target.value)}
            className="h-8 w-20 rounded border border-neutral-300 px-2 text-sm" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-neutral-600">Reassign open tasks to role
          <select value={role} onChange={(e) => setRole(e.target.value)}
            className="h-8 rounded border border-neutral-300 px-2 text-sm">
            <option value="">— leave unchanged —</option>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <button type="button" onClick={apply} disabled={busy}
          className="h-8 rounded bg-amber-600 px-3 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50">
          {busy ? 'Applying…' : 'Apply'}
        </button>
      </div>
      {msg && <p className="mt-2 text-xs text-neutral-700">{msg}</p>}
      <p className="mt-1 text-[11px] text-neutral-400">Does not re-run the engine or send notifications. Completed tasks are untouched.</p>
    </div>
  )
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors.

```bash
git add portal/src/pages/admin/data-maintenance/ApprovalStatePanel.tsx
git commit -m "feat(admin-ui): ApprovalStatePanel manual override"
```

---

### Task 18: Sectioned RecordEditForm wiring

**Files:**
- Modify: `portal/src/pages/admin/data-maintenance/RecordEditForm.tsx`

- [ ] **Step 1: Rewrite RecordEditForm to sectioned layout**

Replace `RecordEditForm.tsx` with a version that: (a) fetches the full record (with line items) via `adminApi.get`, (b) renders reference fields with `ReferencePicker`, scalars with existing inputs, (c) renders `LineItemsEditor` when `schema.child`, (d) renders `ApprovalStatePanel` for pr/po/pa, (e) shows a "Regenerate PO number for new vendor" checkbox (default checked) for PO with a confirm dialog on save when the vendor changed.

```tsx
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { EntitySchema, FieldSpec, RefHit } from '@/services/adminApi'
import { adminApi } from '@/services/adminApi'
import { useAdminEdit } from '@/hooks/useAdmin'
import { ReferencePicker } from './ReferencePicker'
import { LineItemsEditor, type LineRow } from './LineItemsEditor'
import { ApprovalStatePanel } from './ApprovalStatePanel'

interface Props { schema: EntitySchema; record: Record<string, unknown>; onClose: () => void }

export function RecordEditForm({ schema, record, onClose }: Props) {
  const id = String(record.id)
  const { data: full } = useQuery({
    queryKey: ['admin-record', schema.system, schema.key, id],
    queryFn: () => adminApi.get(schema.system, schema.key, id),
  })
  const [form, setForm] = useState<Record<string, string>>({})
  const [refLabels, setRefLabels] = useState<Record<string, string>>({})
  const [refDirty, setRefDirty] = useState<Record<string, string>>({})   // name → new id
  const [lines, setLines] = useState<LineRow[]>([])
  const [regenPo, setRegenPo] = useState(true)
  const [vendorChanged, setVendorChanged] = useState(false)
  const [error, setError] = useState('')
  const edit = useAdminEdit(schema.system, schema.key)

  useEffect(() => {
    if (!full) return
    setForm(Object.fromEntries(schema.fields.map((f) => [f.name, full[f.name] == null ? '' : String(full[f.name])])))
    setRefLabels(Object.fromEntries(schema.fields.filter((f) => f.type === 'reference')
      .map((f) => [f.name, f.ref_name_field ? String(full[f.ref_name_field] ?? '') : String(full[f.name] ?? '')])))
    setLines((full.line_items as LineRow[] | undefined) ?? [])
  }, [full, schema])

  const isRef = (f: FieldSpec) => f.type === 'reference'
  const pickRef = (f: FieldSpec, hit: RefHit) => {
    setRefDirty((p) => ({ ...p, [f.name]: hit.id }))
    setRefLabels((p) => ({ ...p, [f.name]: hit.label }))
    if (f.name === 'vendor_id') setVendorChanged(true)
  }

  const save = async () => {
    setError('')
    if (schema.key === 'po' && vendorChanged && regenPo &&
        !window.confirm('Regenerate the PO number for the new vendor? This rewrites the number across PR/PA/GR/invoice/tasks/approval records.'))
      return
    const editable = schema.fields.filter((f) => f.editable)
    const patch: Record<string, unknown> = {}
    for (const f of editable) {
      if (isRef(f)) { if (refDirty[f.name] !== undefined) patch[f.name] = refDirty[f.name] }
      else patch[f.name] = form[f.name] === '' ? null : form[f.name]
    }
    if (schema.child) patch.line_items = lines
    try {
      await adminApi.editWithOptions(schema.system, schema.key, id, patch,
        { regeneratePoNumber: schema.key === 'po' && vendorChanged && regenPo })
      await edit.reset?.()
      onClose()
    } catch (e) { setError(e instanceof Error ? e.message : 'Save failed') }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="max-h-[88vh] w-full max-w-3xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
        <h3 className="mb-4 text-base font-semibold">Edit {schema.label} · {String(record[schema.number_field] ?? '')}</h3>

        <section className="mb-5 grid grid-cols-2 gap-3">
          {schema.fields.map((f) => (
            <div key={f.name} className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-600">{f.label}{!f.editable && ' (read-only)'}</label>
              {isRef(f) && f.editable ? (
                <ReferencePicker system={schema.system} source={f.ref_source ?? ''}
                  valueLabel={refLabels[f.name] ?? ''} onPick={(h) => pickRef(f, h)} />
              ) : f.type === 'enum' && f.options ? (
                <select value={form[f.name] ?? ''} disabled={!f.editable}
                  onChange={(e) => setForm((p) => ({ ...p, [f.name]: e.target.value }))}
                  className="h-9 rounded-lg border border-neutral-300 px-3 text-sm disabled:bg-neutral-100">
                  {!f.options.includes(form[f.name]) && form[f.name] && <option value={form[f.name]}>{form[f.name]} (current)</option>}
                  {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input value={form[f.name] ?? ''} disabled={!f.editable}
                  onChange={(e) => setForm((p) => ({ ...p, [f.name]: e.target.value }))}
                  className="h-9 rounded-lg border border-neutral-300 px-3 text-sm disabled:bg-neutral-100" />
              )}
            </div>
          ))}
        </section>

        {schema.key === 'po' && vendorChanged && (
          <label className="mb-4 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={regenPo} onChange={(e) => setRegenPo(e.target.checked)} />
            Regenerate PO number for the new vendor (cascades across linked records)
          </label>
        )}

        {schema.child && (
          <section className="mb-5">
            <LineItemsEditor child={schema.child} rows={lines} onChange={setLines} />
          </section>
        )}

        {['pr', 'po', 'pa'].includes(schema.key) && (
          <section className="mb-5">
            <ApprovalStatePanel system={schema.system} entity={schema.key} recordId={id}
              currentStep={full?.approval_step_idx == null ? null : Number(full.approval_step_idx)} />
          </section>
        )}

        {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100">Cancel</button>
          <button onClick={save} className="rounded-lg bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700">Save</button>
        </div>
      </div>
    </div>
  )
}
```

> `approval_step_idx` isn't in the schema `fields` for the panel's current step, but `get_record` returns it only if it's a schema field. Add `FieldSpec("approval_step_idx", "number", False)` to PR/PO/PA schemas (read-only) so it serializes, OR have `edit_approval_state`'s GET path include it. Simplest: add the read-only field to the three schemas (Task 4/6 follow-up — add it there).
> `useAdminEdit(...).reset` may not exist; if the hook has no `reset`, invalidate the query via `queryClient.invalidateQueries(['admin', schema.system, schema.key])` instead — check `portal/src/hooks/useAdmin.ts` for the exact invalidation key and mirror it.

- [ ] **Step 2: Add read-only `approval_step_idx` to PR/PO/PA schemas**

In `epms-api/app/admin/registry.py`, add `FieldSpec("approval_step_idx", "number", False)` to `_PR_SCHEMA`, `_PO_SCHEMA`, `_PA_SCHEMA` fields. (Backfills the panel's current-step display.)

- [ ] **Step 3: Typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors. Fix any hook-signature mismatches per the note above.

- [ ] **Step 4: Commit**

```bash
git add portal/src/pages/admin/data-maintenance/RecordEditForm.tsx epms-api/app/admin/registry.py
git commit -m "feat(admin-ui): sectioned edit form — references, line items, approval, PO regen"
```

---

## Phase 7 — Verification & wrap-up

### Task 19: Full regression + manual smoke

- [ ] **Step 1: Backend suites (serial)**

Run: `cd epms-api && python -m pytest tests/test_admin.py tests/test_admin_edit_expansion.py -v`
Run: `cd approval-api && python -m pytest tests/ -k "resync or routing" -v`
Expected: all PASS. Record pass counts (positive evidence, per `feedback_verification_positive_evidence`).

- [ ] **Step 2: Frontend typecheck + lint (single-file, against baseline)**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors vs baseline. (Portal is clean per memory; epms frontend is not touched here.)

- [ ] **Step 3: Manual smoke (dev stack)** — rebuild the running epms-api/approval-api/portal dev containers so the new endpoints are live (per the admin memory note: containers serve old images until rebuilt). Then in Portal → Admin → Data Maintenance:
  - Edit a PR: change Requester via picker → save → confirm approval task reassigned (check Task Inbox / approval-api resync log line)
  - Edit a PO: change Vendor with regen checked → confirm new number + linked PR/PA/GR show the new number
  - Edit a PO: add/delete a line item → confirm subtotal/tax/total recomputed
  - Edit a PA: use "Source Requester" → confirm the linked PR's creator changed

- [ ] **Step 4: Final commit / branch status**

```bash
git status
git log --oneline feature/data-maintenance-edit-expansion ^main | head -30
```

Confirm all work is on `feature/data-maintenance-edit-expansion`. Do NOT merge to main or build/push images without the user's go-ahead (release discipline R4/R5, and `feedback_uniops_strict_version_control`).

---

## Self-Review Notes

- **Spec coverage:** §3 metadata → T1; §4 references → T2/T3/T4; §5 requester→routing → T11/T12/T13; §6 line items → T5/T6/T7/T8; §7 approval state → T9; §8 PO number → T10; §9 frontend → T14–T18; §10 audit/errors/tests interleaved (audit in T3/T9/T10, 422s in T3, tests throughout).
- **Cross-service:** the resync endpoint (T11) is approval-api; its trigger (T12) is epms-api post-commit best-effort. PA B-1 (T13) edits the source PR's creator.
- **No migration:** every column/table referenced already exists (verified against the ORM models in the spec).
- **Open implementation checks flagged inline:** exact `useAdmin` invalidation key (T18), how the bearer token reaches admin handlers (T12), `CostCenter` display columns (T2), route ordering for `/lookup` (T14).
