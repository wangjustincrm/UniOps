# Invoice 多 PO 行级分摊 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一张 EPMS 发票能分摊到多个 PO 的多个行，并按 PO 行做三方匹配，发票为单一状态单位。

**Architecture:** 新增 `invoice_po_allocations` 明细表作为分摊真相源；发票行加稳定 id 支撑「发票行→PO行」拖拽归类；三方匹配从「单 PO」改为「提交一组分摊」，做整票完整性校验 + 逐 PO 行 variance + 整票状态汇总。发票头保留 `po_id`/`po_total`/`variance` 等字段作汇总值与向后兼容（照搬已有「多 GR」模式）。`match()` 对旧的单 PO 请求做归一化（合成一条 PO 头级分摊），使现有测试、PATCH 重匹配、改造前的前端继续工作。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + asyncpg + Alembic（epms-api）；React + TypeScript 6 + TanStack Query + Tailwind（epms 前端）。

**约定（本轮 Git 策略）：** 本轮所有变更**不提交 Git**，统一在需求批次结束后由用户一次性提交。因此每个 Task 末尾的「Checkpoint」只运行验证、把改动留在工作区，**不要 `git commit` / `git push` / 建分支**。

**测试库说明：** `epms-api/tests/conftest.py` 用 `Base.metadata.create_all` 从模型直接建表（非 alembic）。所以新模型一旦定义，测试库自动有该表，无需为测试跑迁移。运行测试需本地 docker `uniops_postgres` 上的 `epms_test` 库（见 `feedback_uniops_admin_test_db_env`），首次：`python -m scripts.create_test_db`。

**类型口径（贯穿全程，后续任务复用同名签名）：**
- `AllocationInput`：`invoice_line_id: uuid.UUID`、`po_id: uuid.UUID`、`po_line_id: uuid.UUID | None`、`allocated_amount: Decimal`、`allocated_tax: Decimal = 0`、`note: str | None`
- `InvoicePoAllocation` ORM 字段：`invoice_id, invoice_line_id, po_id, po_line_id, allocated_amount, allocated_tax, allocated_total, variance, variance_pct, note`
- `crud.invoice.match()` 签名不变：`async def match(db, invoice, req, matched_by) -> Invoice`

---

## File Structure

**epms-api（后端）**
- `app/models/invoice_allocation.py` — 新建：`InvoicePoAllocation` ORM
- `app/models/__init__.py` — 修改：注册新模型（供 `Base.metadata` 与测试 create_all 发现）
- `app/schemas/invoice.py` — 修改：`InvoiceLineItem.id`、`AllocationInput`、`AllocationResponse`、扩展 `InvoiceMatchRequest`、`InvoiceResponse.allocations`
- `app/crud/invoice.py` — 修改：`create()` 补行 id、`get_by_id()` 预载 allocations、`match()` 重写、`get_all()`/`is_visible()` scope 改造
- `app/api/v1/invoices.py` — 修改：PATCH 重匹配路径适配 allocations
- `alembic/versions/y1_invoice_po_allocations.py` — 新建：建表迁移
- `scripts/migrate_invoice_allocations.py` — 新建：存量发票回填 allocation
- `tests/test_invoice_allocations.py` — 新建：多 PO 行级分摊测试

**epms（前端）**
- `src/services/invoices.ts` — 修改：`InvoiceLineItem.id`、`InvoiceAllocation`、`MatchInvoiceBody.allocations`、`ApiInvoice.allocations`、`invoiceService.match` 透传 allocations
- `src/hooks/useInvoices.ts` — 修改：`useMatchInvoice` 接受 allocations
- `src/pages/invoices/InvoiceAllocationPanel.tsx` — 新建：发票行→PO 行 拖拽分摊面板
- `src/pages/invoices/InvoiceListPage.tsx` — 修改：内联 match 面板改用 `InvoiceAllocationPanel`
- `src/pages/invoices/InvoiceDetailPage.tsx` — 修改：3-Way Match 标签展示多 PO 分摊明细

---

## Task 1: 发票行加稳定 id

**Files:**
- Modify: `epms-api/app/schemas/invoice.py:9-14`（`InvoiceLineItem`）
- Modify: `epms-api/app/crud/invoice.py:144`（`create()` 写 line_items 处）
- Test: `epms-api/tests/test_invoice_allocations.py`

- [ ] **Step 1: 写失败测试**

新建 `epms-api/tests/test_invoice_allocations.py`：

```python
"""Multi-PO line-level allocation tests."""
import pytest

INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"
PO_URL = "/api/v1/po"

_PO_LINE = {"description": "Widget", "qty": "10", "unit": "EA", "unit_price": "100.00"}


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "Alloc Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_issued_po(client, vendor_id, lines=None):
    po = await client.post(PO_URL, json={
        "title": "Alloc Test PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": lines or [_PO_LINE],
    })
    po.raise_for_status()
    po_id = po.json()["id"]
    for act in ["submit", "approve", "approve", "issue"]:
        (await client.post(f"{PO_URL}/{po_id}/action", json={"action": act})).raise_for_status()
    # re-fetch to get line item ids
    full = await client.get(f"{PO_URL}/{po_id}")
    full.raise_for_status()
    return full.json()


def _inv_payload(vendor_id, **overrides):
    base = {
        "vendor_id": vendor_id, "vendor_invoice_number": "INV-ALLOC-001",
        "amount": "1000.00", "tax_amount": "130.00", "currency": "CAD",
        "invoice_date": "2026-03-20", "due_date": "2026-04-19",
        "line_items": [{"description": "Detail A", "quantity": "1",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_invoice_line_items_get_stable_id(admin_client):
    v = await _make_vendor(admin_client, "VND-ALLOC-LINEID-01")
    r = await admin_client.post(INV_URL, json=_inv_payload(v["id"]))
    assert r.status_code == 201, r.text
    lines = r.json()["line_items"]
    assert len(lines) == 1
    assert lines[0]["id"]  # backend assigned a uuid
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py::test_invoice_line_items_get_stable_id -v`
Expected: FAIL（`KeyError: 'id'` 或断言失败，line item 无 `id`）

- [ ] **Step 3: 实现 — schema 加可选 id**

`epms-api/app/schemas/invoice.py` 的 `InvoiceLineItem` 改为：

```python
import uuid as _uuid

class InvoiceLineItem(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid.uuid4)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), ge=0)
    unit: str | None = Field(default=None, max_length=50)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    line_total: Decimal = Field(default=Decimal("0"), ge=0)
```

`create()`（`app/crud/invoice.py:144`）写 line_items 的方式不变（`item.model_dump(mode="json")` 已含 id）。确认 `mode="json"` 会把 uuid 序列化为字符串，存进 JSONB。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py::test_invoice_line_items_get_stable_id -v`
Expected: PASS

- [ ] **Step 5: 回归既有发票测试**

Run: `cd epms-api && python -m pytest tests/test_invoices.py -v`
Expected: 全部 PASS（默认 `default_factory` 不破坏既有断言）

- [ ] **Step 6: Checkpoint（不提交）**

改动留工作区，不 `git commit`。

---

## Task 2: `InvoicePoAllocation` ORM 模型

**Files:**
- Create: `epms-api/app/models/invoice_allocation.py`
- Modify: `epms-api/app/models/__init__.py`
- Test: `epms-api/tests/test_invoice_allocations.py`

> 实现注意（`feedback_uniops_mirror_models_match_reality`）：本表是**新表**（非镜像既有物理表），由本计划同时定义模型与迁移，二者必须列对列一致；`PoLineItem` 仅用 `UUIDPrimaryKey`（无时间戳），本表按 spec 需要时间戳，故用 `UUIDPrimaryKey + TimestampMixin`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_invoice_allocations.py` 追加：

```python
@pytest.mark.asyncio
async def test_allocation_model_importable_and_mapped():
    from app.models.invoice_allocation import InvoicePoAllocation
    cols = {c.name for c in InvoicePoAllocation.__table__.columns}
    assert {"invoice_id", "invoice_line_id", "po_id", "po_line_id",
            "allocated_amount", "allocated_tax", "allocated_total",
            "variance", "variance_pct", "note"} <= cols
    assert InvoicePoAllocation.__tablename__ == "invoice_po_allocations"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py::test_allocation_model_importable_and_mapped -v`
Expected: FAIL（`ModuleNotFoundError: app.models.invoice_allocation`）

- [ ] **Step 3: 实现模型**

`epms-api/app/models/invoice_allocation.py`：

```python
"""ORM model for Invoice ↔ PO line allocation (multi-PO line-level split)."""
import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class InvoicePoAllocation(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "invoice_po_allocations"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    # stable id of the invoice JSONB line this allocation came from (traceability)
    invoice_line_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )
    # target PO line; NULL = PO-header-level allocation (fallback)
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("po_line_items.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )

    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)   # pre-tax
    allocated_tax: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    allocated_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    variance: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
```

在 `epms-api/app/models/__init__.py` 增加导入（与现有模型导入风格一致，确保 `Base.metadata` 注册）：

```python
from app.models.invoice_allocation import InvoicePoAllocation  # noqa: F401
```

> 先打开 `app/models/__init__.py` 确认现有导入写法（是否有 `__all__`），照其风格补一行。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py::test_allocation_model_importable_and_mapped -v`
Expected: PASS

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 3: 分摊 Pydantic schemas + 扩展请求/响应

**Files:**
- Modify: `epms-api/app/schemas/invoice.py`
- Test: `epms-api/tests/test_invoice_allocations.py`

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_allocation_schemas_exist():
    from app.schemas.invoice import AllocationInput, AllocationResponse, InvoiceMatchRequest
    # InvoiceMatchRequest accepts an allocations list (new path)
    req = InvoiceMatchRequest(allocations=[])
    assert req.allocations == []
    # legacy single-PO fields remain optional
    import uuid
    legacy = InvoiceMatchRequest(po_id=uuid.uuid4())
    assert legacy.allocations is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py::test_allocation_schemas_exist -v`
Expected: FAIL（`ImportError: AllocationInput`）

- [ ] **Step 3: 实现 schemas**

`epms-api/app/schemas/invoice.py`：新增并修改如下。

```python
class AllocationInput(BaseModel):
    invoice_line_id: uuid.UUID
    po_id: uuid.UUID
    po_line_id: uuid.UUID | None = None
    allocated_amount: Decimal = Field(ge=0)   # pre-tax
    allocated_tax: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = None


class AllocationResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_line_id: uuid.UUID
    po_id: uuid.UUID
    po_line_id: uuid.UUID | None
    allocated_amount: Decimal
    allocated_tax: Decimal
    allocated_total: Decimal
    variance: Decimal | None
    variance_pct: Decimal | None
    note: str | None
    model_config = {"from_attributes": True}
```

修改现有 `InvoiceMatchRequest`，把所有字段设为可选并加 `allocations`：

```python
class InvoiceMatchRequest(BaseModel):
    # New multi-PO path: when provided, takes priority.
    allocations: list[AllocationInput] | None = None
    # Legacy single-PO path (existing tests / PATCH re-match / pre-rework UI).
    po_id: uuid.UUID | None = None
    gr_id: uuid.UUID | None = None
    gr_ids: list[uuid.UUID] | None = None
    po_line_ids: list[uuid.UUID] | None = None
```

在 `InvoiceResponse` 增加（放在 `model_config` 之前）：

```python
    allocations: list[AllocationResponse] = Field(default_factory=list)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py::test_allocation_schemas_exist -v`
Expected: PASS

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 4: 重写 `match()` — 分摊归一化 + 完整性校验 + 逐 PO 行 variance + 整票汇总

**Files:**
- Modify: `epms-api/app/crud/invoice.py:161-271`（`match()`）
- Test: `epms-api/tests/test_invoice_allocations.py`

这是核心。`match()` 接受新 `allocations` 或旧单 PO 请求；旧请求归一化为「一条 PO 头级分摊（整票额）」，从而单一代码路径。

- [ ] **Step 1: 写失败测试（多 PO 行级，全部容差内）**

追加：

```python
@pytest.mark.asyncio
async def test_match_two_pos_line_level_matched(admin_client):
    """发票分摊到两个 PO 各一行,合计=发票总额,各行零差 → matched。"""
    v = await _make_vendor(admin_client, "VND-ALLOC-2PO-01")
    po_a = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "A", "qty": "1", "unit": "EA", "unit_price": "600.00"}])
    po_b = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "B", "qty": "1", "unit": "EA", "unit_price": "400.00"}])
    line_a = po_a["line_items"][0]["id"]
    line_b = po_b["line_items"][0]["id"]

    # invoice: pre-tax 1000, tax 0 → total 1000; one invoice line
    inv = await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "combined", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))
    inv = inv.json()
    inv_line = inv["line_items"][0]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po_a["id"], "po_line_id": line_a,
         "allocated_amount": "600.00", "allocated_tax": "0.00"},
        {"invoice_line_id": inv_line, "po_id": po_b["id"], "po_line_id": line_b,
         "allocated_amount": "400.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert len(data["allocations"]) == 2
    assert data["po_id"] in (po_a["id"], po_b["id"])  # primary = first allocation's PO
```

- [ ] **Step 2: 写失败测试（分摊不平 → 422）**

追加：

```python
@pytest.mark.asyncio
async def test_match_allocations_must_sum_to_total(admin_client):
    v = await _make_vendor(admin_client, "VND-ALLOC-SUM-01")
    po = await _make_issued_po(admin_client, v["id"])
    line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    # only allocate 600 of 1000 → must be rejected
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "600.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 422, r.text
```

- [ ] **Step 3: 写失败测试（某 PO 行超差 → exception）**

追加：

```python
@pytest.mark.asyncio
async def test_match_line_over_variance_exception(admin_client):
    v = await _make_vendor(admin_client, "VND-ALLOC-VAR-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "500.00"}])
    line = po["line_items"][0]["id"]
    # invoice total 1000 allocated to a PO line worth 500 → +100% variance
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "exception"
    assert float(data["allocations"][0]["variance"]) == 500.0
```

- [ ] **Step 4: 运行三测确认失败**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py -k "two_pos or must_sum or over_variance" -v`
Expected: FAIL（旧 `match()` 不认 `allocations`，无 422 校验）

- [ ] **Step 5: 实现 — 重写 `match()`**

替换 `epms-api/app/crud/invoice.py` 中 `match()` 全函数。先在文件顶部 import 增补：

```python
from app.models.invoice_allocation import InvoicePoAllocation
from app.schemas.invoice import AllocationInput
from sqlalchemy import delete as sa_delete
```

新 `match()`：

```python
class AllocationImbalance(ValueError):
    """Raised when allocations don't sum to the invoice total (→ HTTP 422)."""


async def _normalize_allocations(invoice: Invoice, req: InvoiceMatchRequest) -> list[AllocationInput]:
    """Return the effective allocation list. Legacy single-PO requests become one
    PO-header-level allocation covering the full invoice total."""
    if req.allocations is not None:
        return req.allocations
    if req.po_id is None:
        raise ValueError("Either allocations or po_id is required")
    # legacy: synthesize a single head-level allocation on the first invoice line
    line_id = None
    if invoice.line_items:
        line_id = invoice.line_items[0].get("id")
    if line_id is None:
        line_id = str(uuid.uuid4())
    po_line_id = req.po_line_ids[0] if req.po_line_ids else None
    return [AllocationInput(
        invoice_line_id=uuid.UUID(str(line_id)),
        po_id=req.po_id,
        po_line_id=po_line_id,
        allocated_amount=invoice.amount,
        allocated_tax=invoice.tax_amount,
    )]


async def match(
    db: AsyncSession,
    invoice: Invoice,
    req: InvoiceMatchRequest,
    matched_by: uuid.UUID,
) -> Invoice:
    now = datetime.now(timezone.utc)
    allocs = await _normalize_allocations(invoice, req)
    if not allocs:
        raise ValueError("At least one allocation is required")

    # 1. integrity: sum(allocated_total) must equal invoice.total_amount
    alloc_total = sum((a.allocated_amount + a.allocated_tax for a in allocs), Decimal("0"))
    if abs(alloc_total - invoice.total_amount) > Decimal("0.01"):
        raise AllocationImbalance(
            f"Allocations total {alloc_total} must equal invoice total {invoice.total_amount}"
        )

    # 2. validate referenced POs/lines, cache PO objects
    po_cache: dict[uuid.UUID, PurchaseOrder] = {}
    for a in allocs:
        if a.po_id not in po_cache:
            po = (await db.execute(
                select(PurchaseOrder).where(PurchaseOrder.id == a.po_id)
            )).scalar_one_or_none()
            if po is None:
                raise ValueError(f"Purchase order {a.po_id} not found")
            po_cache[a.po_id] = po
        if a.po_line_id is not None:
            exists = (await db.execute(
                select(PoLineItem.id)
                .where(PoLineItem.id == a.po_line_id, PoLineItem.po_id == a.po_id)
            )).scalar_one_or_none()
            if exists is None:
                raise ValueError(f"PO line {a.po_line_id} not on PO {a.po_id}")

    # 3. rebuild allocations (idempotent)
    await db.execute(sa_delete(InvoicePoAllocation).where(InvoicePoAllocation.invoice_id == invoice.id))

    tolerance = await _match_tolerance_pct(db)
    any_exception = False
    new_rows: list[InvoicePoAllocation] = []
    for a in allocs:
        allocated_total = a.allocated_amount + a.allocated_tax
        row = InvoicePoAllocation(
            invoice_id=invoice.id,
            invoice_line_id=a.invoice_line_id,
            po_id=a.po_id,
            po_line_id=a.po_line_id,
            allocated_amount=a.allocated_amount,
            allocated_tax=a.allocated_tax,
            allocated_total=allocated_total,
            note=a.note,
        )
        db.add(row)
        new_rows.append(row)
    await db.flush()  # assign ids; rows now queryable

    # 4. per (po_id, po_line_id) variance vs reference, across ALL invoices
    for row in new_rows:
        po = po_cache[row.po_id]
        if row.po_line_id is not None:
            reference = (await db.execute(
                select(PoLineItem.line_total).where(PoLineItem.id == row.po_line_id)
            )).scalar_one()
        else:
            reference = po.total

        q = select(func.coalesce(func.sum(InvoicePoAllocation.allocated_total), Decimal("0"))) \
            .where(InvoicePoAllocation.po_id == row.po_id)
        if row.po_line_id is not None:
            q = q.where(InvoicePoAllocation.po_line_id == row.po_line_id)
        else:
            q = q.where(InvoicePoAllocation.po_line_id.is_(None))
        invoiced = (await db.execute(q)).scalar_one() or Decimal("0")

        variance = invoiced - reference
        variance_pct = (
            (variance / reference * 100).quantize(Decimal("0.0001"))
            if reference != Decimal("0") else Decimal("0")
        )
        row.variance = variance
        row.variance_pct = variance_pct
        if not within_tolerance(variance, variance_pct, tolerance):
            any_exception = True

    # 5. roll up to invoice header (summary + backward-compat single values)
    first = allocs[0]
    primary_po = po_cache[first.po_id]
    invoice.po_id = primary_po.id
    invoice.po_number = primary_po.number
    invoice.matched_at = now
    invoice.matched_by = matched_by
    invoice.matched_by_name = (await db.execute(
        select(User.full_name).where(User.id == matched_by)
    )).scalar_one_or_none()

    # summary reference total = sum of distinct PO-line (or PO) references touched
    seen: set[tuple] = set()
    summary_reference = Decimal("0")
    for row in new_rows:
        key = (row.po_id, row.po_line_id)
        if key in seen:
            continue
        seen.add(key)
        if row.po_line_id is not None:
            summary_reference += (await db.execute(
                select(PoLineItem.line_total).where(PoLineItem.id == row.po_line_id)
            )).scalar_one()
        else:
            summary_reference += po_cache[row.po_id].total
    invoice.po_total = summary_reference
    invoice.variance = invoice.total_amount - summary_reference
    invoice.variance_pct = (
        (invoice.variance / summary_reference * 100).quantize(Decimal("0.0001"))
        if summary_reference != Decimal("0") else Decimal("0")
    )
    # matched_po_line_ids/matched_reference_total no longer written (superseded)
    invoice.matched_po_line_ids = None
    invoice.matched_reference_total = None

    # GR handling unchanged (whole-invoice level) — reuse existing logic
    effective_gr_ids: list[uuid.UUID] = []
    if req.gr_ids:
        effective_gr_ids = list(req.gr_ids)
    elif req.gr_id:
        effective_gr_ids = [req.gr_id]
    if effective_gr_ids:
        gr_value_total = Decimal("0")
        gr_numbers: list[str] = []
        first_gr_id: uuid.UUID | None = None
        for gid in effective_gr_ids:
            gr_obj = (await db.execute(select(GoodsReceipt).where(GoodsReceipt.id == gid))).scalar_one_or_none()
            if gr_obj:
                if first_gr_id is None:
                    first_gr_id = gr_obj.id
                gr_numbers.append(gr_obj.number)
                gr_value_total += sum((it.line_total for it in gr_obj.line_items), Decimal("0"))
        invoice.gr_id = first_gr_id
        invoice.gr_number = ", ".join(gr_numbers) if gr_numbers else None
        invoice.gr_value = gr_value_total
        invoice.gr_ids = [str(gid) for gid in effective_gr_ids]
    else:
        invoice.gr_id = None
        invoice.gr_number = None
        invoice.gr_value = None
        invoice.gr_ids = None

    if any_exception:
        invoice.status = "exception"
        invoice.exception_reason = (
            "One or more PO lines are outside tolerance "
            f"(invoice total {invoice.total_amount} vs reference {summary_reference})"
        )
    else:
        invoice.status = "matched"
        if invoice.variance != Decimal("0"):
            invoice.exception_reason = (
                f"Auto-matched within tolerance {tolerance}% (variance: {invoice.variance:+.2f})"
            )

    await db.flush()
    await db.refresh(invoice)
    return invoice
```

> 删除旧 `match()` 体；保留 `within_tolerance`、`_match_tolerance_pct`、`_next_ref` 等辅助函数不动。

- [ ] **Step 6: 在端点上把 `AllocationImbalance` 映射为 422**

`epms-api/app/api/v1/invoices.py` 的 `match_invoice`（约 189-215 行），把 `except ValueError` 拆成两层：

```python
    from app.crud.invoice import AllocationImbalance
    try:
        result = await invoice_crud.match(db, inv, body, matched_by=uuid.UUID(user["sub"]))
        await _notify_requester_create_pa(db, result)
        if result.status == "matched":
            await finance_client.post_invoice(invoice_id=result.id, bearer_token=token)
        return result
    except AllocationImbalance as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

- [ ] **Step 7: 运行新测 + 回归既有匹配测试**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py tests/test_invoices.py -v`
Expected: 全部 PASS（旧单 PO 测试经归一化路径仍通过；新多 PO 测试通过）

- [ ] **Step 8: Checkpoint（不提交）**

---

## Task 5: `get_by_id` 预载 allocations + 响应序列化

**Files:**
- Modify: `epms-api/app/crud/invoice.py:84-86`（`get_by_id`）
- Test: `epms-api/tests/test_invoice_allocations.py`

发票头默认不带关系；`InvoiceResponse.allocations` 需要数据。最稳妥：`get_by_id` 单独查 allocations 并挂到对象上供 `from_attributes` 读取。

- [ ] **Step 1: 写失败测试**

追加：

```python
@pytest.mark.asyncio
async def test_get_invoice_returns_allocations(admin_client):
    v = await _make_vendor(admin_client, "VND-ALLOC-GET-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    r = await admin_client.get(f"{INV_URL}/{inv['id']}")
    assert r.status_code == 200
    allocs = r.json()["allocations"]
    assert len(allocs) == 1
    assert allocs[0]["po_id"] == po["id"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py::test_get_invoice_returns_allocations -v`
Expected: FAIL（`allocations` 为空列表）

- [ ] **Step 3: 实现**

`get_by_id` 改为加载 allocations 并赋到瞬态属性：

```python
async def get_by_id(db: AsyncSession, invoice_id: uuid.UUID) -> Invoice | None:
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = result.scalar_one_or_none()
    if inv is None:
        return None
    rows = (await db.execute(
        select(InvoicePoAllocation)
        .where(InvoicePoAllocation.invoice_id == inv.id)
        .order_by(InvoicePoAllocation.created_at)
    )).scalars().all()
    inv.allocations = list(rows)   # transient attr read by InvoiceResponse(from_attributes)
    return inv
```

> `Invoice` ORM 未声明 `allocations` 关系，直接赋普通属性即可被 Pydantic `from_attributes` 读取。若运行时报 SQLAlchemy 对未知属性赋值告警，则在 `Invoice` 模型加 `allocations` relationship（`lazy="noload"`）或 `__init__` 默认空列表；首选先尝试直接赋值。

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py tests/test_invoices.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 6: 权限可见性按 allocations 改造

**Files:**
- Modify: `epms-api/app/crud/invoice.py:48-108`（`get_all` / `is_visible`）
- Test: `epms-api/tests/test_invoice_allocations.py`

现按 `Invoice.po_id ∈ po_subq` 过滤；改为「主 po_id 命中 **或** 存在 allocation.po_id 命中」。

- [ ] **Step 1: 写失败测试**

> 该测试需要一个受限 scope 的用户。先查 `tests/conftest.py` 与既有权限测试，确认是否有现成的「requester / 受限范围」client fixture；若无，则按既有 scope 测试（搜索 `po_subq` 的现有测试）的构造方式建一个。占位思路：

```python
@pytest.mark.asyncio
async def test_visibility_via_allocation_po(admin_client):
    """admin 不受限,作为基线断言:含多 PO 分摊的发票能被按其中任一 PO 过滤到。"""
    v = await _make_vendor(admin_client, "VND-ALLOC-VIS-01")
    po_a = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "A", "qty": "1", "unit": "EA", "unit_price": "600.00"}])
    po_b = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "B", "qty": "1", "unit": "EA", "unit_price": "400.00"}])
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po_a["id"], "po_line_id": po_a["line_items"][0]["id"],
         "allocated_amount": "600.00", "allocated_tax": "0.00"},
        {"invoice_line_id": inv_line, "po_id": po_b["id"], "po_line_id": po_b["line_items"][0]["id"],
         "allocated_amount": "400.00", "allocated_tax": "0.00"},
    ]})
    # filter by the NON-primary PO (po_b is second allocation) → still found
    r = await admin_client.get(f"{INV_URL}?po_id={po_b['id']}")
    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert inv["id"] in ids
```

> 注意：当前 `get_all` 的 `po_id` 过滤器（line 74-75）按 `Invoice.po_id == po_id`；本测试要求按 allocation 过滤，因此该过滤器也要改（见 Step 3）。

- [ ] **Step 2: 运行确认失败**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py::test_visibility_via_allocation_po -v`
Expected: FAIL（按非主 PO 过滤不到）

- [ ] **Step 3: 实现**

在 `get_all` 顶部构造「发票 → 命中 allocation」的子查询，并改两处过滤：

```python
async def get_all(db, *, status=None, vendor_id=None, po_id=None,
                  po_ids_subq=None, own_uploads_user_id=None, page=1, page_size=20):
    from sqlalchemy import or_
    from app.models.invoice_allocation import InvoicePoAllocation
    q = select(Invoice)

    if po_ids_subq is not None or own_uploads_user_id is not None:
        scope_conds = []
        if po_ids_subq is not None:
            alloc_scope = select(InvoicePoAllocation.invoice_id).where(
                InvoicePoAllocation.po_id.in_(po_ids_subq)
            )
            scope_conds.append(Invoice.po_id.in_(po_ids_subq))
            scope_conds.append(Invoice.id.in_(alloc_scope))
        if own_uploads_user_id is not None:
            scope_conds.append(Invoice.uploaded_by == own_uploads_user_id)
        q = q.where(or_(*scope_conds))

    if status:
        q = q.where(Invoice.status == status)
    if vendor_id:
        q = q.where(Invoice.vendor_id == vendor_id)
    if po_id:
        alloc_by_po = select(InvoicePoAllocation.invoice_id).where(
            InvoicePoAllocation.po_id == po_id
        )
        q = q.where(or_(Invoice.po_id == po_id, Invoice.id.in_(alloc_by_po)))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    offset = (page - 1) * page_size
    items = list((await db.execute(
        q.order_by(Invoice.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all())
    return items, total
```

同理改 `is_visible`：

```python
async def is_visible(db, invoice, scope) -> bool:
    from sqlalchemy import or_
    from app.models.invoice_allocation import InvoicePoAllocation
    po_ids_subq = scope["po_subq"]; user_id = scope["user_id"]; role = scope["role"]
    conds = []
    if po_ids_subq is not None:
        alloc_scope = select(InvoicePoAllocation.invoice_id).where(
            InvoicePoAllocation.po_id.in_(po_ids_subq)
        )
        conds.append(Invoice.po_id.in_(po_ids_subq))
        conds.append(Invoice.id.in_(alloc_scope))
    if role == "requester":
        conds.append(Invoice.uploaded_by == user_id)
    if not conds:
        return True
    result = await db.execute(
        select(Invoice.id).where(Invoice.id == invoice.id).where(or_(*conds))
    )
    return result.scalar_one_or_none() is not None
```

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py tests/test_invoices.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 7: PATCH 重匹配路径适配 allocations

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py:147-186`（`update_invoice`）
- Modify: `epms-api/app/crud/invoice.py`（新增 `rematch_from_existing` 辅助）
- Test: `epms-api/tests/test_invoice_allocations.py`

当前 PATCH 后用 `InvoiceMatchRequest(po_id=prev_po_id, ...)` 重匹配（单 PO）。多分摊发票若金额变了，旧分摊会与新总额不平。规则：**编辑已分摊发票时，按现有 allocations 重算 variance；若发票总额变化导致与分摊合计不平，则清空分摊并回到 `unmatched`（要求重新分摊）。**

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_edit_amount_unbalances_resets_to_unmatched(admin_client):
    v = await _make_vendor(admin_client, "VND-ALLOC-EDIT-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1",
                     "unit_price": "1000.00", "line_total": "1000.00"}]))).json()
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"], "po_line_id": line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    # change pre-tax amount → allocations (1000) no longer equal new total (800)
    r = await admin_client.patch(f"{INV_URL}/{inv['id']}", json={"amount": "800.00"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "unmatched"
    assert data["allocations"] == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py::test_edit_amount_unbalances_resets_to_unmatched -v`
Expected: FAIL（现路径会走单 PO 重匹配或报错，不会清分摊回 unmatched）

- [ ] **Step 3: 实现 — crud 辅助**

`app/crud/invoice.py` 新增：

```python
async def rematch_from_existing(db: AsyncSession, invoice: Invoice, matched_by: uuid.UUID) -> Invoice:
    """Re-run match using the invoice's CURRENT allocations. If they no longer sum
    to the (possibly edited) total, clear them and reset to unmatched."""
    rows = (await db.execute(
        select(InvoicePoAllocation).where(InvoicePoAllocation.invoice_id == invoice.id)
    )).scalars().all()
    if not rows:
        return invoice
    alloc_total = sum((r.allocated_total for r in rows), Decimal("0"))
    if abs(alloc_total - invoice.total_amount) > Decimal("0.01"):
        await db.execute(sa_delete(InvoicePoAllocation).where(InvoicePoAllocation.invoice_id == invoice.id))
        invoice.status = "unmatched"
        invoice.po_id = None
        invoice.po_number = None
        invoice.po_total = None
        invoice.variance = None
        invoice.variance_pct = None
        invoice.matched_at = None
        await db.flush()
        await db.refresh(invoice)
        return invoice
    # balanced → recompute via match() using current allocations
    req = InvoiceMatchRequest(allocations=[
        AllocationInput(
            invoice_line_id=r.invoice_line_id, po_id=r.po_id, po_line_id=r.po_line_id,
            allocated_amount=r.allocated_amount, allocated_tax=r.allocated_tax, note=r.note,
        ) for r in rows
    ], gr_ids=[uuid.UUID(g) for g in invoice.gr_ids] if invoice.gr_ids else None)
    return await match(db, invoice, req, matched_by)
```

- [ ] **Step 4: 实现 — 端点改用辅助**

`update_invoice`（`app/api/v1/invoices.py`）中把「if prev_po_id is not None」整块替换为：

```python
    result = await invoice_crud.update(db, inv, body)

    # Re-run match against current allocations so variance/status reflect edits.
    if prev_po_id is not None or (inv.gr_ids):
        result = await invoice_crud.rematch_from_existing(db, result, matched_by=uuid.UUID(user["sub"]))
        if result.status == "matched":
            await finance_client.post_invoice(invoice_id=result.id, bearer_token=token)

    return result
```

> 保留函数顶部 `prev_po_id` / `prev_gr_ids` 的捕获（仍用于判断是否曾匹配）。`gr_ids` 的显式重选逻辑本期简化：GR 仍整票级，编辑时若传 `gr_ids` 已由 `update()`/模型处理；如需保留旧「显式清空 GR」语义，可在 `rematch_from_existing` 前按 `body.gr_ids` 更新 `invoice.gr_ids`。实现时先跑测试 `test_invoices.py` 看 GR 相关用例是否回归。

- [ ] **Step 5: 运行确认通过 + 全量回归**

Run: `cd epms-api && python -m pytest tests/test_invoice_allocations.py tests/test_invoices.py -v`
Expected: 全部 PASS。若 `test_invoices.py` 中 GR 重匹配用例回归，按 Step 4 注释补 `gr_ids` 处理再跑。

- [ ] **Step 6: Checkpoint（不提交）**

---

## Task 8: Alembic 迁移 — 建 `invoice_po_allocations` 表

**Files:**
- Create: `epms-api/alembic/versions/y1_invoice_po_allocations.py`

> 测试用 create_all，不依赖此迁移；此迁移用于真实库。`down_revision` 必须指向**当前 head**。

- [ ] **Step 1: 确认当前 head**

Run（在能用 alembic 的环境，如容器内）：`cd epms-api && python -m alembic heads`
Expected: 单一 head `x8_add_tax_code_snapshot`（截至本计划编写时）。若本轮其他 WIP 又加了迁移导致 head 变化或多 head，则以实际输出为准，并把下方 `down_revision` 改成该 head（多 head 时本迁移可作为 merge 点，`down_revision = (head1, head2)`）。

- [ ] **Step 2: 写迁移**

`epms-api/alembic/versions/y1_invoice_po_allocations.py`：

```python
"""create invoice_po_allocations (multi-PO line-level allocation)

Revision ID: y1_invoice_po_allocations
Revises: x8_add_tax_code_snapshot
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "y1_invoice_po_allocations"
down_revision = "x8_add_tax_code_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invoice_po_allocations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_line_id", UUID(as_uuid=True), nullable=False),
        sa.Column("po_id", UUID(as_uuid=True),
                  sa.ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("po_line_id", UUID(as_uuid=True),
                  sa.ForeignKey("po_line_items.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("allocated_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("allocated_tax", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("allocated_total", sa.Numeric(15, 2), nullable=False),
        sa.Column("variance", sa.Numeric(15, 2), nullable=True),
        sa.Column("variance_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoice_po_allocations_invoice_id", "invoice_po_allocations", ["invoice_id"])
    op.create_index("ix_invoice_po_allocations_po_id", "invoice_po_allocations", ["po_id"])
    op.create_index("ix_invoice_po_allocations_po_line_id", "invoice_po_allocations", ["po_line_id"])


def downgrade():
    op.drop_index("ix_invoice_po_allocations_po_line_id", table_name="invoice_po_allocations")
    op.drop_index("ix_invoice_po_allocations_po_id", table_name="invoice_po_allocations")
    op.drop_index("ix_invoice_po_allocations_invoice_id", table_name="invoice_po_allocations")
    op.drop_table("invoice_po_allocations")
```

- [ ] **Step 3: 校验迁移可升降（本地/容器）**

Run: `cd epms-api && python -m alembic upgrade head && python -m alembic downgrade -1 && python -m alembic upgrade head`
Expected: 无错误；表创建/回滚成功。

> 若本机无法连库（见 `feedback_uniops_admin_test_db_env`），跳过实跑，但务必目检 `down_revision` 与列定义同 Task 2 模型完全一致。

- [ ] **Step 4: Checkpoint（不提交）**

---

## Task 9: 存量发票数据迁移脚本

**Files:**
- Create: `epms-api/scripts/migrate_invoice_allocations.py`

把每张已有 `po_id` 的发票转成**一条 PO 头级 allocation**（`po_line_id=NULL`，`allocated_total = total_amount`），并给发票行补 id。零风险保平：不改发票头的 status/variance。

- [ ] **Step 1: 写脚本**

`epms-api/scripts/migrate_invoice_allocations.py`：

```python
"""Backfill invoice_po_allocations for existing single-PO invoices.

Idempotent: skips invoices that already have allocations. Also assigns stable
ids to JSONB line items that lack one.

Run:  python -m scripts.migrate_invoice_allocations
"""
import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import select, func
from app.db.session import AsyncSessionLocal
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation


async def run() -> None:
    async with AsyncSessionLocal() as db:
        invoices = (await db.execute(select(Invoice))).scalars().all()
        created = 0
        for inv in invoices:
            # 1. ensure line items have stable ids
            changed = False
            items = list(inv.line_items or [])
            for it in items:
                if not it.get("id"):
                    it["id"] = str(uuid.uuid4())
                    changed = True
            if changed:
                inv.line_items = items

            # 2. skip if no PO link or already allocated
            if inv.po_id is None:
                continue
            existing = (await db.execute(
                select(func.count()).select_from(InvoicePoAllocation)
                .where(InvoicePoAllocation.invoice_id == inv.id)
            )).scalar_one()
            if existing:
                continue

            line_id = items[0]["id"] if items else str(uuid.uuid4())
            db.add(InvoicePoAllocation(
                invoice_id=inv.id,
                invoice_line_id=uuid.UUID(line_id),
                po_id=inv.po_id,
                po_line_id=None,
                allocated_amount=inv.amount,
                allocated_tax=inv.tax_amount,
                allocated_total=inv.total_amount,
                variance=inv.variance,
                variance_pct=inv.variance_pct,
                note="Backfilled from single-PO invoice",
            ))
            created += 1
        await db.commit()
        print(f"Backfill complete: {created} allocations created.")


if __name__ == "__main__":
    asyncio.run(run())
```

- [ ] **Step 2: 干跑验证（测试库）**

> 在 `epms_test` 或本地 docker `uniops_postgres` 的 epms 库上跑一次，确认无异常、计数合理。

Run: `cd epms-api && python -m scripts.migrate_invoice_allocations`
Expected: 打印 `Backfill complete: N allocations created.`，再次运行 N=0（幂等）。

- [ ] **Step 3: Checkpoint（不提交）**

---

## Task 10: 前端类型 + service + hook

**Files:**
- Modify: `epms/src/services/invoices.ts`
- Modify: `epms/src/hooks/useInvoices.ts`

- [ ] **Step 1: 改 service 类型**

`epms/src/services/invoices.ts`：

`InvoiceLineItem` 加 `id`：

```typescript
export interface InvoiceLineItem {
  id?:         string
  description: string
  quantity:    number
  unit:        string | null
  unit_price:  number
  line_total:  number
}
```

新增分摊类型：

```typescript
export interface InvoiceAllocation {
  id: string
  invoice_id: string
  invoice_line_id: string
  po_id: string
  po_line_id: string | null
  allocated_amount: number
  allocated_tax: number
  allocated_total: number
  variance: number | null
  variance_pct: number | null
  note: string | null
}

export interface AllocationInput {
  invoice_line_id: string
  po_id: string
  po_line_id?: string | null
  allocated_amount: number
  allocated_tax?: number
  note?: string
}
```

`ApiInvoice` 加 `allocations?: InvoiceAllocation[]`。`MatchInvoiceBody` 改为：

```typescript
export interface MatchInvoiceBody {
  allocations?: AllocationInput[]
  po_id?: string
  gr_id?: string
  gr_ids?: string[]
  po_line_ids?: string[]
}
```

`invoiceService.match` 改为接受完整 body：

```typescript
  match: (id: string, body: MatchInvoiceBody) =>
    api.post<ApiInvoice>(`/invoices/${id}/match`, body),
```

- [ ] **Step 2: 改 hook**

`epms/src/hooks/useInvoices.ts` 的 `useMatchInvoice`：

```typescript
import { invoiceService, type InvoiceFilters, type CreateInvoiceBody,
         type UpdateInvoiceBody, type MatchInvoiceBody } from '@/services/invoices'

export function useMatchInvoice() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & MatchInvoiceBody) =>
      invoiceService.match(id, body),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['invoices', id] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}
```

> 这会改变 `useMatchInvoice` 的调用签名。`InvoiceListPage.tsx` 现有两处调用（约 259、808 行）传 `{ id, po_id, gr_id, po_line_ids }` —— 这些字段仍在 `MatchInvoiceBody` 里（向后兼容），TS 不会报错；功能上后端归一化处理。Task 11 会把主分摊入口替换为 allocations。

- [ ] **Step 2.5: typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 无错误（旧调用点因字段保留而兼容）。

- [ ] **Step 3: Checkpoint（不提交）**

---

## Task 11: 前端 — 发票行→PO 行 拖拽分摊面板

**Files:**
- Create: `epms/src/pages/invoices/InvoiceAllocationPanel.tsx`
- Modify: `epms/src/pages/invoices/InvoiceListPage.tsx`（内联 match 面板，约 751-950 行）

> 全部 user-facing 文案纯英文（`feedback_uniops_ui_english_only`）。Decimal 字段 `Number()` 强转后再运算/`toFixed`（`feedback_uniops_decimal_as_string`）。

- [ ] **Step 1: 建分摊面板组件**

`epms/src/pages/invoices/InvoiceAllocationPanel.tsx`（HTML5 原生拖拽，无新依赖）：

```tsx
import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { formatAmount } from '@/lib/utils'
import type { ApiInvoice, InvoiceLineItem, AllocationInput } from '@/services/invoices'
import type { ApiPo } from '@/services/pos'

interface Props {
  invoice: ApiInvoice
  pos: ApiPo[]                 // candidate POs (issued/approved, same vendor)
  onSubmit: (allocations: AllocationInput[]) => void
  submitting?: boolean
}

// invoiceLineId -> { poId, poLineId }
type Assignment = Record<string, { poId: string; poLineId: string }>

export function InvoiceAllocationPanel({ invoice, pos, onSubmit, submitting }: Props) {
  const lines: InvoiceLineItem[] = invoice.line_items ?? []
  const [assign, setAssign] = useState<Assignment>({})
  const [dragLineId, setDragLineId] = useState<string | null>(null)

  const currency = invoice.currency
  const total = Number(invoice.total_amount)

  const allocatedByLine = (lineId: string) => {
    const li = lines.find((l) => l.id === lineId)
    return li ? Number(li.line_total) : 0
  }

  const assignedTotal = useMemo(
    () => Object.keys(assign).reduce((s, lid) => s + allocatedByLine(lid), 0),
    [assign, lines],
  )
  const unallocated = total - assignedTotal
  const balanced = Math.abs(unallocated) < 0.01

  const drop = (poId: string, poLineId: string) => {
    if (!dragLineId) return
    setAssign((prev) => ({ ...prev, [dragLineId]: { poId, poLineId } }))
    setDragLineId(null)
  }

  const clearLine = (lineId: string) =>
    setAssign((prev) => { const n = { ...prev }; delete n[lineId]; return n })

  // group assigned invoice lines per PO line → sum to allocated_amount
  const buildAllocations = (): AllocationInput[] => {
    return Object.entries(assign).map(([invoiceLineId, t]) => ({
      invoice_line_id: invoiceLineId,
      po_id: t.poId,
      po_line_id: t.poLineId,
      // amount is pre-tax line_total; tax handled at header level this phase
      allocated_amount: allocatedByLine(invoiceLineId),
      allocated_tax: 0,
    }))
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-2.5">
        <span className="text-xs text-neutral-500">Unallocated balance</span>
        <span className={`font-mono text-sm font-semibold ${balanced ? 'text-success-600' : 'text-danger-600'}`}>
          {formatAmount(unallocated, currency)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Invoice lines (drag source) */}
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">Invoice lines</h4>
          <div className="flex flex-col gap-2">
            {lines.map((l) => {
              const a = l.id ? assign[l.id] : undefined
              return (
                <div key={l.id}
                  draggable
                  onDragStart={() => setDragLineId(l.id ?? null)}
                  className={`cursor-grab rounded-lg border px-3 py-2 text-sm ${a ? 'border-primary-200 bg-primary-50' : 'border-neutral-200'}`}>
                  <div className="flex justify-between">
                    <span className="truncate">{l.description}</span>
                    <span className="font-mono text-xs">{formatAmount(Number(l.line_total), currency)}</span>
                  </div>
                  {a && (
                    <button onClick={() => l.id && clearLine(l.id)}
                      className="mt-1 text-[11px] text-primary-600 hover:underline">
                      Assigned → unassign
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* PO lines (drop target) */}
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">PO lines (drop here)</h4>
          <div className="flex flex-col gap-3">
            {pos.map((po) => (
              <div key={po.id}>
                <p className="mb-1 font-mono text-xs text-neutral-500">{po.number}</p>
                {po.line_items.map((pl) => {
                  const allocatedHere = Object.entries(assign)
                    .filter(([, t]) => t.poLineId === pl.id)
                    .reduce((s, [lid]) => s + allocatedByLine(lid), 0)
                  return (
                    <div key={pl.id}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => drop(po.id, pl.id)}
                      className="mb-1 rounded-lg border border-dashed border-neutral-300 px-3 py-2 text-sm hover:border-primary-400">
                      <div className="flex justify-between">
                        <span className="truncate">{pl.description}</span>
                        <span className="font-mono text-xs">{formatAmount(Number(pl.line_total), po.currency)}</span>
                      </div>
                      <p className="text-[11px] text-neutral-400">
                        Allocated {formatAmount(allocatedHere, po.currency)} ·
                        Remaining {formatAmount(Number(pl.line_total) - allocatedHere, po.currency)}
                      </p>
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex justify-end">
        <Button onClick={() => onSubmit(buildAllocations())} disabled={!balanced || submitting}>
          {submitting ? 'Matching…' : 'Confirm allocation & match'}
        </Button>
      </div>
    </div>
  )
}
```

> 校验 `@/services/pos` 中 `ApiPo` 的实际导出名与 `line_items` 字段名（打开该文件确认；若类型名不同则改 import）。

- [ ] **Step 2: 在 InvoiceListPage 接入**

打开 `epms/src/pages/invoices/InvoiceListPage.tsx`，定位内联 match 面板（约 751-950 行，`useMatchInvoice` + 单 PO 选择 + `po_line_ids`）。把其「选 PO + 选行」主体替换为 `InvoiceAllocationPanel`，提交时调用：

```tsx
matchInvoiceMutation.mutate({ id: invoice.id, allocations })
```

候选 `pos` 沿用面板现有的 `matchablePOs` 过滤（issued/approved + 同 vendor）。保留 GR 选择 UI（整票级），提交时一并传 `gr_ids`（可选）。

> 这是较大的 UI 改造。保持页面包在现有 EPMS chrome 内（`feedback_uniops_portal_page_chrome`），不要新建裸 div 布局。删除已不再用的 `selectedPoLineIds` 单 PO 状态。

- [ ] **Step 3: typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 无错误。

- [ ] **Step 4: 构建确认**

Run: `cd epms && npm run build`
Expected: 构建成功（注意 TS6 baseUrl 弃用，typecheck 用上面的命令；build 若因弃用报错，以 typecheck 为准并记录）。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 12: 前端 — 详情页展示多 PO 分摊明细

**Files:**
- Modify: `epms/src/pages/invoices/InvoiceDetailPage.tsx`（3-Way Match 标签，约 750-860 行）

当前 match 表只显示单个 `inv.po_number` 行。改为：若 `inv.allocations` 多于一条，渲染按 PO/PO 行的分摊明细表（PO、PO 行、分摊额、variance）。

- [ ] **Step 1: 实现明细表**

在 3-Way Match 标签内，匹配表上方或下方插入（当 `inv.allocations && inv.allocations.length > 0`）：

```tsx
{inv.allocations && inv.allocations.length > 0 && (
  <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-neutral-200 bg-neutral-50">
          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">PO</th>
          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">PO Line</th>
          <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Allocated</th>
          <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Variance</th>
        </tr>
      </thead>
      <tbody>
        {inv.allocations.map((a) => (
          <tr key={a.id} className="border-b border-neutral-100">
            <td className="px-4 py-3">
              <Link to={`/po/${a.po_id}`} className="font-mono text-xs text-primary-600 hover:underline">
                {a.po_id.slice(0, 8)}
              </Link>
            </td>
            <td className="px-4 py-3 text-xs text-neutral-500">{a.po_line_id ? a.po_line_id.slice(0, 8) : 'PO header'}</td>
            <td className="px-4 py-3 text-right font-mono text-xs">{formatAmount(Number(a.allocated_total), inv.currency)}</td>
            <td className={cn('px-4 py-3 text-right font-mono text-xs',
              Number(a.variance ?? 0) === 0 ? 'text-success-600' : 'text-danger-600')}>
              {a.variance == null ? '—' : formatAmount(Number(a.variance), inv.currency)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
)}
```

> 可选增强：用 PO 列表/缓存把 `a.po_id` 映射为 PO number 显示，而非截断 id。本期截断显示即可（YAGNI），但要保留 PO 链接可点。

- [ ] **Step 2: typecheck + build**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 无错误。

- [ ] **Step 3: Checkpoint（不提交）**

---

## Final Verification

- [ ] **后端全量测试**

Run: `cd epms-api && python -m pytest tests/test_invoices.py tests/test_invoice_allocations.py tests/test_invoice_tax_lines.py -v`
Expected: 全部 PASS。

- [ ] **前端 typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 无错误。

- [ ] **不提交**：确认所有改动留在工作区，等本轮批次结束由用户统一提交。

---

## 验收对照（spec → 任务）

| spec 需求 | 任务 |
|---|---|
| 发票行加稳定 id | Task 1、Task 9(回填) |
| `invoice_po_allocations` 表（方案 A，行级） | Task 2、Task 8 |
| 分摊 schemas + 扩展请求/响应 | Task 3 |
| 整票完整性校验（分完才 match，422） | Task 4(Step 2/6) |
| 逐 PO 行 variance + 整票状态汇总 | Task 4 |
| 旧单 PO 兼容（测试/PATCH/旧前端） | Task 4(归一化) |
| allocations 出现在响应 | Task 3、Task 5 |
| 权限按 allocation.po_id 可见 | Task 6 |
| 编辑重匹配处理 | Task 7 |
| 存量数据迁移 | Task 9 |
| 前端 service/hook | Task 10 |
| 发票行→PO 行拖拽分摊 UI | Task 11 |
| 详情页分摊明细展示 | Task 12 |
| GR 维持整票级（非目标） | Task 4(GR 块不变) |
| 付款/预算入账（非目标） | 不在本计划 |
