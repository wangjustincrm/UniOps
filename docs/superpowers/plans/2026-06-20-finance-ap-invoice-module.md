# Finance AP Invoice 主模块 Implementation Plan (Plan 1 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 finance-api 新建 finance 自有的 AP Invoice 主模块（`ap_invoices` + 税行 + CRUD/upsert + API + accrual 改读 + open-items/aging 改读），成为应付发票真相源。

**Architecture:** 完全对照 finance 已有的 AR 模块（`ar_invoices` / `crud/ar.py` / `api/v1/ar.py`）建对称的 AP 模块。EPMS/OA 通过 `POST /finance/v1/ap/invoices` 幂等 upsert 发票头+税行（按 `source+source_invoice_id`）；status 转 `posted` 时触发 AP accrual（幂等）。`/ap/open-items`、`/ap/aging`、accrual 从只读镜像 `mirrors.Invoice` 切到 `ap_invoices`。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + asyncpg + Alembic（finance-api）。测试：pytest + 本地 docker `uniops_postgres` 的 `finance_test` 库（conftest 跑真实 alembic）。

**本轮 Git 策略：** 所有变更**不提交**（批次统一提交）。每个 Task 末尾「Checkpoint」只验证、留工作区，**不要 git commit/push/建分支/stash**。

**生产库说明：** dev 容器连 10.10.50.20（生产库，当前无正式数据，可当测试库）；改 finance-api 文件会经 `--reload` 即时生效，无需过度谨慎。单测走本地 `finance_test`（非生产库）。

**整体分解（本 spec 拆 5 个 plan）：**
1. **本计划**：finance AP 模块（表/crud/API/accrual/ap 改读）— 基础，独立可测。
2. EPMS 接入（`finance_client.upsert_ap_invoice` + create/match/update/delete 钩子）。
3. OA/expense-api 接入（新增 finance client + 生命周期钩子）。
4. 存量回填迁移（epms invoices + oa expense_invoices → ap_invoices）。
5. 前端（Finance AP 管理页 + Aging report）。
Plan 2-5 依赖本计划的 API，在各自执行前再用 writing-plans 展开。

---

## File Structure（本计划）

- `finance-api/app/models/ap_invoice.py` — 新建：`ApInvoice` + `ApInvoiceTaxLine` ORM。
- `finance-api/alembic/versions/0014_ap_invoices.py` — 新建：建表迁移（down_revision=`0013_accounts_receivable`）。
- `finance-api/app/crud/ap_invoice.py` — 新建：`upsert` / `list_invoices` / `get` / `set_void` / `_next_number`。
- `finance-api/app/crud/ap_accrual.py` — 修改：`post_invoice_accrual` 改读 `ApInvoice`/`ApInvoiceTaxLine`。
- `finance-api/app/api/v1/ap_invoices.py` — 新建：`/ap/invoices` 路由（upsert/list/get/void）。
- `finance-api/app/api/v1/ap.py` — 修改：`/ap/open-items`、`/ap/aging` 改读 `ap_invoices`；`/ap/post-invoice` 适配（按 ap_invoice_id 或保留 epms invoice_id 兼容，见 Task 5）。
- `finance-api/app/api/v1/__init__.py` + `app/main.py` — 修改：注册新 router + 模型。
- `finance-api/tests/test_ap_invoice.py` — 新建：crud 级测试。

类型口径（贯穿）：
- `ApInvoice` 字段见 Task 1。状态常量：`DRAFT="draft"`, `POSTED="posted"`, `PARTIALLY_PAID="partially_paid"`, `PAID="paid"`, `VOID="void"`, `OPEN_STATUSES=(POSTED, PARTIALLY_PAID)`。
- `crud.ap_invoice.upsert(db, *, source, source_invoice_id, payload: dict, tax_lines: list[dict]) -> ApInvoice`
- `crud.ap_accrual.post_invoice_accrual(db, ap_invoice_id) -> dict`（签名不变，改实现）。

---

## Task 1: ApInvoice / ApInvoiceTaxLine 模型

**Files:**
- Create: `finance-api/app/models/ap_invoice.py`
- Test: `finance-api/tests/test_ap_invoice.py`

- [ ] **Step 1: 写失败测试**

新建 `finance-api/tests/test_ap_invoice.py`：

```python
"""AP Invoice module tests (finance-owned AP invoices)."""
import uuid
from datetime import date

import pytest


def test_ap_invoice_model_mapped():
    from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine
    cols = {c.name for c in ApInvoice.__table__.columns}
    assert {"ap_invoice_number", "source", "source_invoice_id", "source_ref",
            "vendor_id", "vendor_name", "vendor_invoice_number",
            "amount", "tax_amount", "total_amount", "paid_amount",
            "currency", "invoice_date", "due_date", "status", "source_status",
            "po_id", "po_number", "posted_at", "entity_id"} <= cols
    assert ApInvoice.__tablename__ == "ap_invoices"
    tcols = {c.name for c in ApInvoiceTaxLine.__table__.columns}
    assert {"invoice_id", "line_no", "tax_code", "taxable_base",
            "tax_amount", "recoverable"} <= tcols
```

- [ ] **Step 2: 运行确认失败**

Run: `cd finance-api && python -m pytest tests/test_ap_invoice.py::test_ap_invoice_model_mapped -v`
Expected: FAIL（`ModuleNotFoundError: app.models.ap_invoice`）

- [ ] **Step 3: 实现模型**

`finance-api/app/models/ap_invoice.py`（对照 `app/models/ar.py`）：

```python
"""Accounts Payable — finance-owned AP invoice master (mirror of AR module).

EPMS + OA upload vendor invoices; both are AP invoices owned here. Sources
(epms / oa) keep their own working tables; finance owns the normalized header +
tax lines via upsert keyed on (source, source_invoice_id). The posting spine
stays the single source of truth (accrual on `posted`).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

DRAFT = "draft"
POSTED = "posted"
PARTIALLY_PAID = "partially_paid"
PAID = "paid"
VOID = "void"
OPEN_STATUSES = (POSTED, PARTIALLY_PAID)


class ApInvoice(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ap_invoices"
    __table_args__ = (
        UniqueConstraint("ap_invoice_number", name="uq_ap_invoices_number"),
        UniqueConstraint("source", "source_invoice_id", name="uq_ap_invoices_source"),
    )

    ap_invoice_number: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False, index=True)          # 'epms' | 'oa'
    source_invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)

    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor_invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))      # pre-tax
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))

    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)          # = Issue Date
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)        # Aging basis (nullable: OA)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DRAFT, index=True)
    source_status: Mapped[str | None] = mapped_column(String(30), nullable=True)  # source's raw status

    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ApInvoiceTaxLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ap_invoice_tax_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ap_invoices.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    taxable_base: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    recoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd finance-api && python -m pytest tests/test_ap_invoice.py::test_ap_invoice_model_mapped -v`
Expected: PASS（纯模型映射检查，不触库）

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 2: Alembic 迁移建表

**Files:**
- Create: `finance-api/alembic/versions/0014_ap_invoices.py`
- Modify: `finance-api/app/main.py`（注册模型）

> finance 的 conftest `_migrate()` 跑真实 `alembic upgrade head`，所以本迁移必须能干净升级，后续 crud 测试才有表。

- [ ] **Step 1: 确认 head**

Run: `cd finance-api && grep -rh '^revision' alembic/versions/*.py | tail -3`
Expected: 最新 `revision = "0013_accounts_receivable"`（本迁移 down_revision 指向它）。

- [ ] **Step 2: 写迁移**

`finance-api/alembic/versions/0014_ap_invoices.py`（对照 `0013_accounts_receivable.py` 风格）：

```python
"""create ap_invoices + ap_invoice_tax_lines (finance-owned AP invoice master)

Revision ID: 0014_ap_invoices
Revises: 0013_accounts_receivable
Create Date: 2026-06-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0014_ap_invoices"
down_revision = "0013_accounts_receivable"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ap_invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ap_invoice_number", sa.String(40), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("source_invoice_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_ref", sa.String(40), nullable=True),
        sa.Column("vendor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("vendor_name", sa.String(255), nullable=True),
        sa.Column("vendor_invoice_number", sa.String(100), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("paid_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("source_status", sa.String(30), nullable=True),
        sa.Column("po_id", UUID(as_uuid=True), nullable=True),
        sa.Column("po_number", sa.String(40), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ap_invoice_number", name="uq_ap_invoices_number"),
        sa.UniqueConstraint("source", "source_invoice_id", name="uq_ap_invoices_source"),
    )
    op.create_index("ix_ap_invoices_source", "ap_invoices", ["source"])
    op.create_index("ix_ap_invoices_vendor_id", "ap_invoices", ["vendor_id"])
    op.create_index("ix_ap_invoices_status", "ap_invoices", ["status"])
    op.create_index("ix_ap_invoices_due_date", "ap_invoices", ["due_date"])

    op.create_table(
        "ap_invoice_tax_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("ap_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("taxable_base", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("recoverable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ap_invoice_tax_lines_invoice_id", "ap_invoice_tax_lines", ["invoice_id"])


def downgrade():
    op.drop_table("ap_invoice_tax_lines")
    op.drop_table("ap_invoices")
```

- [ ] **Step 3: 注册模型到 main.py**

`finance-api/app/main.py` 第 8 行的 import 列表追加 `ap_invoice`：

```python
from app.models import ap_invoice, bank, coa, fiscal_period, mirrors, pa, payment, payment_batch, posting  # noqa: F401 — register with metadata
```

- [ ] **Step 4: 验证迁移可升级（本地 finance_test）**

Run: `cd finance-api && TEST_FINANCE_DB=finance_test python -m pytest tests/test_ap_invoice.py::test_ap_invoice_model_mapped -v`
（该测试不触库，仍 PASS；真正建表在 Task 3 首个 db_session 测试时由 conftest `_migrate()` 跑 alembic。若想单独验证迁移：）
Run: `cd finance-api && DATABASE_URL=postgresql+asyncpg://epms:epms_dev@localhost:5432/finance_test python -m alembic upgrade head`
Expected: 升到 `0014_ap_invoices` 无错误。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 3: ap_invoice CRUD（upsert / list / get / void）

**Files:**
- Create: `finance-api/app/crud/ap_invoice.py`
- Test: `finance-api/tests/test_ap_invoice.py`

- [ ] **Step 1: 写失败测试（upsert 幂等 + 字段映射）**

追加到 `tests/test_ap_invoice.py`：

```python
def _payload(**over):
    p = dict(
        source_ref="INV-2026-0001", vendor_id=uuid.uuid4(), vendor_name="ULINE",
        vendor_invoice_number="V-123", amount="1000.00", tax_amount="130.00",
        total_amount="1130.00", currency="CAD",
        invoice_date=date(2026, 6, 17), due_date=date(2026, 7, 17),
        status="draft", source_status="unmatched", po_id=None, po_number=None,
    )
    p.update(over)
    return p


@pytest.mark.anyio
async def test_upsert_creates_then_updates_idempotent(db_session):
    from app.crud import ap_invoice as crud
    src_id = uuid.uuid4()
    inv1 = await crud.upsert(db_session, source="epms", source_invoice_id=src_id,
                             payload=_payload(), tax_lines=[
                                 {"line_no": 1, "tax_code": "HST_ON", "taxable_base": "1000.00",
                                  "tax_amount": "130.00", "recoverable": True}])
    await db_session.commit()
    assert inv1.ap_invoice_number.startswith("AP-")
    assert inv1.status == "draft"
    assert str(inv1.total_amount) == "1130.00"

    # same key → update, no duplicate, number stable
    inv2 = await crud.upsert(db_session, source="epms", source_invoice_id=src_id,
                             payload=_payload(status="posted", source_status="matched"),
                             tax_lines=[])
    await db_session.commit()
    assert inv2.id == inv1.id
    assert inv2.ap_invoice_number == inv1.ap_invoice_number
    assert inv2.status == "posted"

    rows = await crud.list_invoices(db_session, source="epms")
    assert len([r for r in rows if r.source_invoice_id == src_id]) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd finance-api && python -m pytest tests/test_ap_invoice.py::test_upsert_creates_then_updates_idempotent -v`
Expected: FAIL（`ImportError`/无 upsert）。首次跑会触发 conftest `_migrate()` 建表。

- [ ] **Step 3: 实现 crud**

`finance-api/app/crud/ap_invoice.py`（对照 `crud/ar.py`）：

```python
"""AP invoice CRUD — finance-owned, upserted from EPMS/OA by (source, source_invoice_id)."""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine, VOID

_ZERO = Decimal("0")


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


async def _next_number(db: AsyncSession) -> str:
    today = date.today().strftime("%Y%m%d")
    like = f"AP-{today}-%"
    n = (await db.execute(
        select(func.count()).select_from(ApInvoice).where(ApInvoice.ap_invoice_number.like(like))
    )).scalar_one()
    return f"AP-{today}-{n + 1:04d}"


async def upsert(db: AsyncSession, *, source: str, source_invoice_id: uuid.UUID,
                 payload: dict, tax_lines: list[dict]) -> ApInvoice:
    """Create or update an AP invoice keyed on (source, source_invoice_id).
    payload keys: source_ref, vendor_id, vendor_name, vendor_invoice_number,
    amount, tax_amount, total_amount, currency, invoice_date, due_date, status,
    source_status, po_id, po_number. Tax lines are rebuilt each call."""
    inv = (await db.execute(
        select(ApInvoice).where(ApInvoice.source == source,
                                ApInvoice.source_invoice_id == source_invoice_id)
    )).scalar_one_or_none()

    fields = dict(
        source_ref=payload.get("source_ref"),
        vendor_id=payload.get("vendor_id"),
        vendor_name=payload.get("vendor_name"),
        vendor_invoice_number=payload.get("vendor_invoice_number"),
        amount=_q(payload.get("amount", 0)),
        tax_amount=_q(payload.get("tax_amount", 0)),
        total_amount=_q(payload.get("total_amount", 0)),
        currency=payload.get("currency", "CAD"),
        invoice_date=payload["invoice_date"],
        due_date=payload.get("due_date"),
        status=payload.get("status", "draft"),
        source_status=payload.get("source_status"),
        po_id=payload.get("po_id"),
        po_number=payload.get("po_number"),
    )

    if inv is None:
        inv = ApInvoice(
            ap_invoice_number=await _next_number(db),
            source=source, source_invoice_id=source_invoice_id, **fields,
        )
        db.add(inv)
    else:
        for k, v in fields.items():
            setattr(inv, k, v)
    await db.flush()

    # rebuild tax lines
    await db.execute(sa_delete(ApInvoiceTaxLine).where(ApInvoiceTaxLine.invoice_id == inv.id))
    for t in tax_lines:
        db.add(ApInvoiceTaxLine(
            invoice_id=inv.id, line_no=t["line_no"], tax_code=t.get("tax_code"),
            taxable_base=_q(t.get("taxable_base", 0)), tax_amount=_q(t.get("tax_amount", 0)),
            recoverable=bool(t.get("recoverable", True)),
        ))
    await db.flush()
    return inv


async def list_invoices(db: AsyncSession, *, source: str | None = None,
                        vendor_id: uuid.UUID | None = None, status: str | None = None,
                        limit: int = 500) -> list[ApInvoice]:
    q = select(ApInvoice)
    if source:
        q = q.where(ApInvoice.source == source)
    if vendor_id:
        q = q.where(ApInvoice.vendor_id == vendor_id)
    if status:
        q = q.where(ApInvoice.status == status)
    return list((await db.execute(
        q.order_by(ApInvoice.invoice_date.desc()).limit(limit)
    )).scalars().all())


async def get(db: AsyncSession, invoice_id: uuid.UUID) -> ApInvoice | None:
    return (await db.execute(select(ApInvoice).where(ApInvoice.id == invoice_id))).scalar_one_or_none()


async def get_tax_lines(db: AsyncSession, invoice_id: uuid.UUID) -> list[ApInvoiceTaxLine]:
    return list((await db.execute(
        select(ApInvoiceTaxLine).where(ApInvoiceTaxLine.invoice_id == invoice_id)
        .order_by(ApInvoiceTaxLine.line_no)
    )).scalars().all())


async def set_void(db: AsyncSession, invoice_id: uuid.UUID) -> ApInvoice | None:
    inv = await get(db, invoice_id)
    if inv is None:
        return None
    inv.status = VOID
    await db.flush()
    return inv
```

- [ ] **Step 4: 运行确认通过**

Run: `cd finance-api && python -m pytest tests/test_ap_invoice.py -v`
Expected: 全部 PASS（模型 + upsert 幂等）。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 4: AP accrual 改读 ap_invoices

**Files:**
- Modify: `finance-api/app/crud/ap_accrual.py`
- Test: `finance-api/tests/test_ap_invoice.py`

把 accrual 从只读镜像 `mirrors.Invoice` 切到 finance 自有的 `ApInvoice`/`ApInvoiceTaxLine`，posting 逻辑不变（debit purchase_expense / debit sales_tax(ITC) / credit accounts_payable）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ap_invoice.py`：

```python
@pytest.mark.anyio
async def test_post_invoice_accrual_reads_ap_invoice(db_session):
    from app.crud import ap_invoice as crud
    from app.crud.ap_accrual import post_invoice_accrual
    src_id = uuid.uuid4()
    inv = await crud.upsert(db_session, source="epms", source_invoice_id=src_id,
                            payload=_payload(status="posted", source_status="matched"),
                            tax_lines=[{"line_no": 1, "tax_code": "HST_ON",
                                        "taxable_base": "1000.00", "tax_amount": "130.00",
                                        "recoverable": True}])
    await db_session.commit()
    res = await post_invoice_accrual(db_session, inv.id)
    await db_session.commit()
    assert res["invoice_id"] == inv.id
    assert res["posting_event_id"] is not None       # first post emits
    # idempotent: second call no-ops
    res2 = await post_invoice_accrual(db_session, inv.id)
    await db_session.commit()
    assert res2["already_accrued"] is True
```

- [ ] **Step 2: 运行确认失败**

Run: `cd finance-api && python -m pytest tests/test_ap_invoice.py::test_post_invoice_accrual_reads_ap_invoice -v`
Expected: FAIL（现 `post_invoice_accrual` 读 `mirrors.Invoice`，传 ap_invoice.id 找不到 → LookupError）

- [ ] **Step 3: 实现**

改 `finance-api/app/crud/ap_accrual.py`：把 import 与读取换成 ApInvoice。替换 import 行 `from app.models.mirrors import Invoice, InvoiceTaxLine` 为：

```python
from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine, OPEN_STATUSES
```

把函数体内：
- `select(Invoice).where(Invoice.id == invoice_id)` → `select(ApInvoice).where(ApInvoice.id == invoice_id)`，变量名 `inv` 不变。
- 状态校验 `if inv.status not in ("matched", "approved", "paid")` → `if inv.status not in (OPEN_STATUSES + ("paid",))`（即 posted/partially_paid/paid 才可应计）。错误信息改 `"AP invoice in status '{inv.status}' is not accruable (needs posted)"`。
- `select(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == invoice_id)` → `select(ApInvoiceTaxLine).where(ApInvoiceTaxLine.invoice_id == invoice_id)`，`.order_by(ApInvoiceTaxLine.line_no)`。
- `emit_event(... source_doc_type="invoice", source_doc_id=inv.id, source_doc_number=inv.internal_ref ...)` → `source_doc_type="ap_invoice"`, `source_doc_id=inv.id`, `source_doc_number=inv.ap_invoice_number`。

其余 posting 行（purchase_expense / sales_tax / accounts_payable 用 inv.amount/tax_amount/total_amount/vendor_id/vendor_name/currency/invoice_date）保持不变——字段名在 ApInvoice 上同名，无需改。

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `cd finance-api && python -m pytest tests/test_ap_invoice.py -v`
Expected: 全部 PASS。
Run（确认没破坏现有 finance 测试）: `cd finance-api && python -m pytest tests/ -q`
Expected: 既有套件不因本改动新增失败（accrual 的旧调用方在 Task 5 适配；若有测试直接断言 `post_invoice_accrual` 读 mirror Invoice，按新语义更新该测试）。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 5: API 路由 /ap/invoices + ap.py 改读

**Files:**
- Create: `finance-api/app/api/v1/ap_invoices.py`
- Modify: `finance-api/app/api/v1/__init__.py`（注册 router）
- Modify: `finance-api/app/api/v1/ap.py`（open-items/aging 改读 ap_invoices；post-invoice 适配）
- Test: `finance-api/tests/test_ap_invoice.py`

- [ ] **Step 1: 写失败测试（crud 级的 aging 改读）**

> open-items/aging 的 HTTP 层依赖 CurrentUser；本计划测试走 crud/函数级。为 aging 改读加一个直接测试：

追加到 `tests/test_ap_invoice.py`：

```python
@pytest.mark.anyio
async def test_open_items_and_aging_read_ap_invoices(db_session):
    from app.crud import ap_invoice as crud
    from app.crud.ap_invoice import open_items, aging  # added in this task
    await crud.upsert(db_session, source="oa", source_invoice_id=uuid.uuid4(),
                      payload=_payload(status="posted", due_date=date(2000, 1, 1)), tax_lines=[])
    await db_session.commit()
    items = await open_items(db_session)
    assert len(items) >= 1
    rows = await aging(db_session)
    assert any(Decimal(r["d90_plus"]) > 0 for r in rows)   # very overdue invoice lands in 90+
```

- [ ] **Step 2: 运行确认失败**

Run: `cd finance-api && python -m pytest tests/test_ap_invoice.py::test_open_items_and_aging_read_ap_invoices -v`
Expected: FAIL（`ImportError: open_items/aging`）

- [ ] **Step 3: 实现 crud 的 open_items/aging（对照 crud/ar.py）**

在 `finance-api/app/crud/ap_invoice.py` 追加（`date` 已 import）：

```python
async def open_items(db: AsyncSession, vendor_id: uuid.UUID | None = None,
                     limit: int = 500) -> list[ApInvoice]:
    from app.models.ap_invoice import OPEN_STATUSES
    q = select(ApInvoice).where(ApInvoice.status.in_(OPEN_STATUSES))
    if vendor_id:
        q = q.where(ApInvoice.vendor_id == vendor_id)
    return list((await db.execute(q.order_by(ApInvoice.due_date.nullslast()).limit(limit))).scalars().all())


async def aging(db: AsyncSession) -> list[dict]:
    from app.models.ap_invoice import OPEN_STATUSES
    rows = (await db.execute(
        select(ApInvoice).where(ApInvoice.status.in_(OPEN_STATUSES))
    )).scalars().all()
    today = date.today()
    buckets: dict[tuple, dict[str, Decimal]] = {}
    for r in rows:
        key = (r.vendor_id, r.vendor_name or "", r.currency)
        b = buckets.setdefault(key, {k: _ZERO for k in
                                     ("current", "d1_30", "d31_60", "d61_90", "d90_plus")})
        outstanding = _q(r.total_amount - r.paid_amount)
        # null due_date → treat as current (no due date = not overdue)
        overdue = (today - r.due_date).days if r.due_date is not None else 0
        slot = ("current" if overdue <= 0 else
                "d1_30" if overdue <= 30 else
                "d31_60" if overdue <= 60 else
                "d61_90" if overdue <= 90 else "d90_plus")
        b[slot] += outstanding
    return [
        {"vendor_id": str(vid) if vid else None, "vendor_name": vname, "currency": cur,
         "current": str(b["current"]), "d1_30": str(b["d1_30"]), "d31_60": str(b["d31_60"]),
         "d61_90": str(b["d61_90"]), "d90_plus": str(b["d90_plus"]),
         "total": str(sum(b.values(), _ZERO))}
        for (vid, vname, cur), b in sorted(buckets.items(), key=lambda kv: kv[0][1])
    ]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd finance-api && python -m pytest tests/test_ap_invoice.py::test_open_items_and_aging_read_ap_invoices -v`
Expected: PASS

- [ ] **Step 5: 实现 API 路由**

`finance-api/app/api/v1/ap_invoices.py`（对照 `api/v1/ar.py`，鉴权用 `_require_manage` + `CurrentUser`）：

```python
"""AP Invoice API — finance-owned AP invoices upserted from EPMS/OA."""
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coa import _require_manage
from app.core.deps import CurrentUser
from app.crud import ap_invoice as crud
from app.crud.ap_accrual import post_invoice_accrual
from app.db.base import get_db
from app.models.ap_invoice import POSTED

router = APIRouter(prefix="/ap/invoices", tags=["accounts-payable-invoices"])


class TaxLineIn(BaseModel):
    line_no: int
    tax_code: str | None = None
    taxable_base: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    recoverable: bool = True


class UpsertIn(BaseModel):
    source: str = Field(pattern="^(epms|oa)$")
    source_invoice_id: uuid.UUID
    source_ref: str | None = None
    vendor_id: uuid.UUID | None = None
    vendor_name: str | None = None
    vendor_invoice_number: str | None = None
    amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    currency: str = "CAD"
    invoice_date: date
    due_date: date | None = None
    status: str = "draft"
    source_status: str | None = None
    po_id: uuid.UUID | None = None
    po_number: str | None = None
    tax_lines: list[TaxLineIn] = []


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ap_invoice_number: str
    source: str
    source_invoice_id: uuid.UUID
    source_ref: str | None
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    vendor_invoice_number: str | None
    amount: str
    tax_amount: str
    total_amount: str
    paid_amount: str
    currency: str
    invoice_date: date
    due_date: date | None
    status: str
    source_status: str | None
    po_id: uuid.UUID | None
    po_number: str | None

    @field_validator("amount", "tax_amount", "total_amount", "paid_amount", mode="before")
    @classmethod
    def _s(cls, v): return str(v)


@router.post("", response_model=InvoiceOut)
async def upsert_invoice(body: UpsertIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    inv = await crud.upsert(
        db, source=body.source, source_invoice_id=body.source_invoice_id,
        payload=body.model_dump(exclude={"source", "source_invoice_id", "tax_lines"}),
        tax_lines=[t.model_dump() for t in body.tax_lines],
    )
    # posted → emit AP accrual (idempotent), fail-soft inside posting key
    if inv.status == POSTED:
        try:
            await post_invoice_accrual(db, inv.id)
        except ValueError:
            pass  # not accruable yet — ignore
    await db.commit()
    return inv


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        source: str | None = Query(default=None),
                        vendor_id: uuid.UUID | None = Query(default=None),
                        status: str | None = Query(default=None),
                        limit: int = Query(default=500, le=2000)):
    return await crud.list_invoices(db, source=source, vendor_id=vendor_id, status=status, limit=limit)


@router.get("/{invoice_id}")
async def get_invoice(invoice_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    inv = await crud.get(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="AP invoice not found")
    tax = await crud.get_tax_lines(db, invoice_id)
    return {"invoice": InvoiceOut.model_validate(inv).model_dump(),
            "tax_lines": [{"line_no": t.line_no, "tax_code": t.tax_code,
                           "taxable_base": str(t.taxable_base), "tax_amount": str(t.tax_amount),
                           "recoverable": t.recoverable} for t in tax]}


@router.post("/{invoice_id}/void", response_model=InvoiceOut)
async def void_invoice(invoice_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    inv = await crud.set_void(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="AP invoice not found")
    await db.commit()
    return inv
```

注册到 `finance-api/app/api/v1/__init__.py`：import 与 include（放在 ap_router 之后）：

```python
from app.api.v1.ap_invoices import router as ap_invoices_router
...
api_router.include_router(ap_invoices_router)
```

- [ ] **Step 6: ap.py open-items/aging 改读 ap_invoices**

改 `finance-api/app/api/v1/ap.py`：`open_items` 与 `aging` 端点改为委托 crud：
- `open_items` 端点体替换为 `rows = await ap_invoice_crud.open_items(db, vendor_id=vendor_id, limit=limit)`，并按 `OpenItem` 拼装（字段从 ApInvoice 取：internal_ref→`ap_invoice_number`、其余同名；`due_date` 可空时 `days_overdue=0`、`due_date` 输出空串或 ISO）。
- `aging` 端点体替换为 `return await ap_invoice_crud.aging(db)`（直接返回 crud 的 dict 列表；保留 `AgingBucketRow` response_model 或改为无 model 的 dict 返回，与 ar.py `/ar/aging` 一致——ar 用无 response_model）。建议：去掉 `/ap/aging` 的 `response_model=list[AgingBucketRow]`，直接返回 crud.aging(db)（对齐 AR）。
- 顶部加 `from app.crud import ap_invoice as ap_invoice_crud`。
- `/ap/post-invoice`：保留端点，但语义改为接收 **ap_invoice_id**（finance 内部 id）。本计划内 EPMS 仍未改（Plan 2 才改），所以**保留旧 `post-invoice` 读 mirror 的兼容路径**不动，新增能力通过 `/ap/invoices` upsert(status=posted) 自动触发 accrual。即：本 Task 不删旧 post-invoice，避免打断现网 EPMS 调用；Plan 2 切换 EPMS 到 upsert 后再清理。

> 注意：`ap.py` 顶部仍 `from app.models.mirrors import Invoice` 的局部 import 只在旧 open-items/aging 里——改完后这些局部 import 删除；旧 `/ap/post-invoice` 用的 `post_invoice_accrual` 现已改读 ApInvoice（Task 4），故旧 post-invoice 传 epms invoice_id 会 404。**因此旧 `/ap/post-invoice` 在本计划后会失效**——这是预期的：EPMS 的 accrual 触发改由 Plan 2 的 upsert(status=posted) 承接。为不破坏现网，本 Task 把旧 `/ap/post-invoice` 改为：查 mirror Invoice 拿到 epms 发票，转调 `crud.upsert(source="epms", ...)` 再触发 accrual（即把旧入口桥接到新模型）。实现见 Step 7。

- [ ] **Step 7: 桥接旧 /ap/post-invoice 到新模型（过渡兼容）**

把 `ap.py` 的 `post_invoice` 端点改为读 mirror `Invoice`（epms 发票）→ 组装 payload upsert 到 `ap_invoices`（source="epms", source_invoice_id=epms invoice.id, status="posted"）→ 触发 accrual：

```python
@router.post("/post-invoice")
async def post_invoice(body: PostInvoiceRequest, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    from app.models.mirrors import Invoice, InvoiceTaxLine
    from app.crud import ap_invoice as ap_invoice_crud
    from app.crud.ap_accrual import post_invoice_accrual
    from sqlalchemy import select
    inv = (await db.execute(select(Invoice).where(Invoice.id == body.invoice_id))).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    tax = (await db.execute(select(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == inv.id)
                            .order_by(InvoiceTaxLine.line_no))).scalars().all()
    ap = await ap_invoice_crud.upsert(
        db, source="epms", source_invoice_id=inv.id,
        payload={"source_ref": inv.internal_ref, "vendor_id": inv.vendor_id,
                 "vendor_name": inv.vendor_name, "vendor_invoice_number": inv.vendor_invoice_number,
                 "amount": inv.amount, "tax_amount": inv.tax_amount, "total_amount": inv.total_amount,
                 "currency": inv.currency, "invoice_date": inv.invoice_date, "due_date": inv.due_date,
                 "status": "posted", "source_status": inv.status},
        tax_lines=[{"line_no": t.line_no, "tax_code": t.tax_code, "tax_amount": t.tax_amount,
                    "recoverable": t.recoverable} for t in tax],
    )
    try:
        result = await post_invoice_accrual(db, ap.id)
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
```

（这样现网 EPMS 的 `post_invoice` 调用在本计划后仍可工作，并已把发票纳入 `ap_invoices`。Plan 2 再把 EPMS 全生命周期 upsert 接上、最终下线本桥接。）

- [ ] **Step 8: 全量回归**

Run: `cd finance-api && python -m pytest tests/ -q`
Expected: 全绿（既有 + 新 test_ap_invoice）。若既有测试断言旧 aging 的 `mirrors.Invoice` 行为，按新 ap_invoices 语义更新。

- [ ] **Step 9: Checkpoint（不提交）**

---

## Final Verification（本计划）

- [ ] Run: `cd finance-api && python -m pytest tests/ -q` → 全绿。
- [ ] Run: `DATABASE_URL=postgresql+asyncpg://epms:epms_dev@localhost:5432/finance_test python -m alembic downgrade -1 && ... upgrade head` → 迁移可升降。
- [ ] 改动留工作区，不提交。

---

## 验收对照（spec → 本计划）

| spec 需求 | 本计划 Task |
|---|---|
| finance 拥有规范化 AP 发票头+税行 | Task 1, 2 |
| upsert 幂等(source+source_invoice_id) | Task 3 |
| posted 触发 accrual(幂等)，改读 ap_invoices | Task 4, 5(upsert) |
| open-items/aging 改读 ap_invoices(due_date,空→current) | Task 5 |
| API 归口(POST/GET/void) | Task 5 |
| 现网 EPMS post-invoice 不中断 | Task 5 Step 7 桥接 |
| EPMS/OA 全量接入 | **Plan 2 / Plan 3** |
| 存量迁移 | **Plan 4** |
| 前端 AP 管理 + Aging | **Plan 5** |
