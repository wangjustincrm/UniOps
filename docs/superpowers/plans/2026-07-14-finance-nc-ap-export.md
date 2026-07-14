# NC AP 导出(并行期) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AP 页勾选已入账应付单 → 按 `AP_template.xlsx` 硬格式生成 NC 应付模块引入 xlsx(billno=UniOps AP 号作复核锚点),批次留痕、AP 标记、可重导。

**Architecture:** spec `2026-07-14-finance-nc-ap-export-design.md`。迁移 0021(批次表+AP 两列)+ 五处镜像扩展 → `services/nc_ap_export.py`(组装纯逻辑:EPMS 经 allocations→PR 维度多行,OA 经 Direct PA 头单行;税分摊;科目全路径)→ xlsx writer(模板副本改造)→ API(POST 导出流+批次+标记,GET 批次)→ AP 页复选+按钮+Exported 列。

**Tech Stack:** FastAPI/SQLAlchemy/alembic/pytest + openpyxl;React。

## Global Constraints

- **分支** `feature/finance-jv-subsystem`(主目录;已并入 main,本子项目继续在此分支,完成后再次合并)。开工前 `git branch --show-current` 确认。
- **⚠️ 工作树有用户未提交 WIP**:只 `git add <指定文件>`,禁止 `-A`/`.`/`stash`/`reset`/`checkout --`。
- **后端测试**:`cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <file> -q`;单进程串行。
- **前端 typecheck**:`cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`,显式 exit 0。
- **新迁移前必查 `alembic heads`**(当前唯一 head=`0020_nc_customers`)。
- **dev 库操作一律容器内跑**(宿主 .env 指生产)。**改 finance-api 代码要 `docker restart uniops_finance_api`**。
- UI 文案纯英文;金额字符串 Number() 强转。
- **镜像忠实物理表**(全部新列 2026-07-14 已按 information_schema 核对,见各 Task)。
- xlsx 硬格式(spec §2):不可增删列;全文本;head 块+空行+body 块;A 列=单序号关联。
- 固定枚举 `NC_EXPORT_DEFAULTS`(spec §3.2 的值,逐字)。

---

## 现状(实现者需知)

- `ap_invoices`(finance 拥有):字段见 `app/models/ap_invoice.py`(ap_invoice_number/source['epms'|'oa']/source_invoice_id/vendor_name/amount/tax_amount/total_amount/currency/invoice_date/status/po_id/po_number + `ApInvoiceTaxLine(tax_code, taxable_base, tax_amount)`)。
- accrual 判定:`posting_events(source_doc_type='ap_invoice', source_doc_id=<ap.id>, event_type='accrual')`;费用科目=该 event 的 `posting_lines` 中 `line_role='purchase_expense'` 行的 `account_code`。
- **维度链(物理列全核对)**:
  - EPMS:`invoice_po_allocations(invoice_id NOT NULL, po_id NOT NULL, allocated_amount NOT NULL, allocated_tax NOT NULL)`;`invoices.po_id(NULL)`;`purchase_requests(po_id NULL, cost_center_id NULL, budget_code NULL varchar, department_name NULL varchar, created_by NOT NULL)`——PR.po_id=PO 反查。
  - OA:`expense_invoices(id, pa_id NULL)` → `payment_applications(cost_center_id NULL, budget_account_code NULL varchar, created_by NOT NULL)`。
- 已有镜像:`mirrors.CostCenter/Department/BudgetAccount/User/Invoice(缺 po_id)/...`、`models/pa.py PaymentApplication`(缺 cost_center_id/budget_account_code/created_by)。conftest 用镜像模型 create_all 建表。
- COA:`chart_of_accounts(code, name, parent_code)`——全路径向上拼。
- 模板:`C:/Project/uniops/AP_template.xlsx`(结构 spec §2;R1 须知/R2 head 技术+列标/R3 head 样例/R4 空/R5 body 技术+列标/R6 body 样例)。openpyxl 尚未在 requirements。
- 权限门:`from app.api.v1.coa import _require_manage`(bank.py 同款用法)。
- 前端 AP 页 `finance/src/pages/finance/AccountsPayablePage.tsx`(219 行,invoices/aging 两 tab;行结构见文件);`InvoiceOut`(api/v1/ap_invoices.py,from_attributes)加字段即出。
- `financeDownload`(GET 版)在 finance/src/lib/api.ts;POST 下载 helper 需新增。

---

### Task 1: 迁移 0021 + 五处镜像扩展

**Files:**
- Create: `finance-api/alembic/versions/0021_nc_export_batches.py`
- Create: `finance-api/app/models/nc_export.py`(NcExportBatch)
- Modify: `finance-api/app/models/ap_invoice.py`(两列)
- Modify: `finance-api/app/models/mirrors.py`(Invoice 加 po_id;新 ExpenseInvoice/InvoicePoAllocation/PurchaseRequest)
- Modify: `finance-api/app/models/pa.py`(PaymentApplication 加三列)
- Modify: `finance-api/app/main.py`(import 列表加 `nc_export`)
- Modify: `finance-api/tests/conftest.py`(新镜像三表注册)
- Test: `finance-api/tests/test_nc_ap_export.py`(新文件)

**Interfaces:**
- Produces: `NcExportBatch`(表 nc_export_batches:exported_by uuid NULL/exported_at tz NOT NULL/ap_count int NOT NULL/filename String(120) NULL);`ApInvoice.nc_exported_at: datetime|None`、`nc_export_batch_id: uuid|None`;`mirrors.Invoice.po_id: uuid|None`;`mirrors.ExpenseInvoice(pa_id: uuid|None)`;`mirrors.InvoicePoAllocation(invoice_id/po_id uuid NOT NULL, allocated_amount/allocated_tax Numeric NOT NULL)`;`mirrors.PurchaseRequest(po_id uuid|None, cost_center_id uuid|None, budget_code String(50)|None, department_name String(255)|None, created_by uuid NOT NULL)`;`pa.PaymentApplication` 加 `cost_center_id uuid|None / budget_account_code String(50)|None / created_by uuid NOT NULL`。

- [ ] **Step 1: 验证 alembic 链尾**

Run: `cd c:/Project/uniops/finance-api && ./.venv/Scripts/python -m alembic heads`
Expected: 恰好 `0020_nc_customers (head)`,否则 STOP。

- [ ] **Step 2: 写失败测试**(新建 tests/test_nc_ap_export.py)

```python
"""NC AP export (parallel-run) — batches, assembly, xlsx, API."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select


async def test_export_batch_and_ap_columns(db_session):
    from app.models.ap_invoice import ApInvoice
    from app.models.nc_export import NcExportBatch
    b = NcExportBatch(exported_by=uuid.uuid4(),
                      exported_at=datetime.now(timezone.utc), ap_count=2,
                      filename="NC-AP-x.xlsx")
    db_session.add(b)
    inv = ApInvoice(ap_invoice_number="AP-2026-0001", source="epms",
                    source_invoice_id=uuid.uuid4(), amount=Decimal("100"),
                    tax_amount=Decimal("13"), total_amount=Decimal("113"),
                    currency="CAD", invoice_date=date(2026, 7, 1), status="posted")
    db_session.add(inv)
    await db_session.flush()
    inv.nc_exported_at = datetime.now(timezone.utc)
    inv.nc_export_batch_id = b.id
    await db_session.flush()
    got = (await db_session.execute(select(ApInvoice).where(
        ApInvoice.id == inv.id))).scalar_one()
    assert got.nc_export_batch_id == b.id


async def test_new_mirror_subsets_readable(db_session):
    from app.models.mirrors import ExpenseInvoice, InvoicePoAllocation, PurchaseRequest
    po = uuid.uuid4()
    db_session.add(PurchaseRequest(id=uuid.uuid4(), po_id=po,
                                   cost_center_id=uuid.uuid4(), budget_code="CRM004",
                                   department_name="Engineering", created_by=uuid.uuid4()))
    db_session.add(InvoicePoAllocation(id=uuid.uuid4(), invoice_id=uuid.uuid4(),
                                       po_id=po, allocated_amount=Decimal("50"),
                                       allocated_tax=Decimal("6.50")))
    db_session.add(ExpenseInvoice(id=uuid.uuid4(), pa_id=uuid.uuid4()))
    await db_session.flush()
    got = (await db_session.execute(select(PurchaseRequest).where(
        PurchaseRequest.po_id == po))).scalar_one()
    assert got.department_name == "Engineering"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py -q`
Expected: 2 FAIL(ModuleNotFoundError)

- [ ] **Step 4: 实现**

(a) `app/models/nc_export.py`(完整文件):

```python
"""NC AP export batches — audit trail for parallel-run exports to NC."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class NcExportBatch(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "nc_export_batches"

    exported_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filename: Mapped[str | None] = mapped_column(String(120), nullable=True)
```

(b) `app/models/ap_invoice.py` —— `entity_id` 行后加:

```python
    nc_exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nc_export_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```

(c) `app/models/mirrors.py`:`Invoice` 类里加 `po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)`;文件末尾(ErpSupplier 后)加:

```python
class ExpenseInvoice(UUIDPrimaryKey, Base):
    """Read-only mirror subset (expense-api owns schema) — OA invoice -> Direct PA
    link for the NC AP export dimension chain. Verified 2026-07-14."""
    __tablename__ = "expense_invoices"

    pa_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class InvoicePoAllocation(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror subset (epms-api owns schema) — line-level multi-PO
    allocation amounts for the NC AP export. Verified 2026-07-14."""
    __tablename__ = "invoice_po_allocations"

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    po_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    allocated_tax: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)


class PurchaseRequest(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror subset (epms-api owns schema) — PR head carries the
    budget dims (CC 100% coverage on dev). Verified 2026-07-14."""
    __tablename__ = "purchase_requests"

    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    budget_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
```

(`Decimal`/`Numeric` import 若缺补上;`ExpenseInvoice` 物理表无 updated_at 之类假设不做——只用 UUIDPrimaryKey,不挂 TimestampMixin,因未核对其时间戳列;`invoice_po_allocations`/`purchase_requests` 物理有 created_at/updated_at,挂 TimestampMixin。)

(d) `app/models/pa.py` —— `submitted_at` 行后加:

```python
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    budget_account_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
```

(e) 迁移 `0021_nc_export_batches.py`(完整文件;只建 finance 自有对象):

```python
"""nc_export_batches + ap_invoices export markers

Revision ID: 0021_nc_export_batches
Revises: 0020_nc_customers
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0021_nc_export_batches"
down_revision = "0020_nc_customers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nc_export_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("exported_by", UUID(as_uuid=True), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filename", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.add_column("ap_invoices",
                  sa.Column("nc_exported_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ap_invoices",
                  sa.Column("nc_export_batch_id", UUID(as_uuid=True), nullable=True))


def downgrade():
    op.drop_column("ap_invoices", "nc_export_batch_id")
    op.drop_column("ap_invoices", "nc_exported_at")
    op.drop_table("nc_export_batches")
```

(f) `app/main.py` import 列表按字母序加 `nc_export`。
(g) `tests/conftest.py`:mirrors import 加 `ExpenseInvoice, InvoicePoAllocation, PurchaseRequest`;create_all tables 列表加三者 `__table__`。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py -q`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/alembic/versions/0021_nc_export_batches.py finance-api/app/models/nc_export.py finance-api/app/models/ap_invoice.py finance-api/app/models/mirrors.py finance-api/app/models/pa.py finance-api/app/main.py finance-api/tests/conftest.py finance-api/tests/test_nc_ap_export.py
git commit -m "feat(finance): nc_export_batches + AP export markers + dimension-chain mirrors (migration 0021)"
```

---

### Task 2: 组装纯逻辑 `services/nc_ap_export.py`

**Files:**
- Create: `finance-api/app/services/nc_ap_export.py`
- Test: `finance-api/tests/test_nc_ap_export.py`(追加)

**Interfaces:**
- Consumes: Task 1 模型/镜像;`app.services.posting.emit_event`(测试造 accrual)。
- Produces(Task 3/4 依赖,签名精确):
  - `NC_EXPORT_DEFAULTS: dict`(键:org/ap_type/busi_process/ap_type_code/buysell/taxtype/tax_code/tax_rate/pay_term/tax_country/obj_type,值 spec §3.2 逐字;obj_type="Supplier")。
  - `TAX_CODE_MAP = {"HST_ON": "001"}`(缺省 fallback NC_EXPORT_DEFAULTS["tax_code"])。
  - `account_full_path(coa: dict[str, ChartOfAccount], code: str) -> str`:`code\名称链(顶级→本级)`,父链经 parent_code;code 不在 COA → 原样返回 code。
  - `async build_export_rows(db, ap_ids: list[uuid.UUID]) -> tuple[list[dict], list[dict], list[dict]]` → `(heads, bodies, errors)`:
    - head dict 键:`seq,int / billno / ap_type / busi_process / billdate / busidate / obj_type / supplier / department / employee / revexp / currency / ap_type_code / tax_country`(str,日期 ISO)。
    - body dict 键:`seq / account_path / invoice_no / summary / pay_term / obj_type / supplier / department / cost_center / employee / revexp / currency / rate / money / qty / tax_code / tax_rate / tax_price / notax / tax / taxtype / department2 / buysell`(金额两位小数字符串)。
    - error dict:`{ap_id, ap_number, reason}`(reason ∈ 'status draft'/'status void'/'no accrual posting')。
  - 语义:费用科目=accrual posting 的 purchase_expense 行 account_code;EPMS body=allocations 行(缺→invoices.po_id 单行,金额=ap.amount/tax_amount),各行经 PR(po_id 反查)取 CC 名/budget_code→BudgetAccount 名/department_name(缺→users[pr.created_by].department 名);OA body=单行,经 ExpenseInvoice.pa_id→PaymentApplication 取 CC/budget_account_code/created_by(employee=users 姓名);税:行 allocated_tax 直接用;OA/单行=ap.tax_amount;行税全 0 且 ap.tax_amount>0 时按 notax 比例摊(尾差进末行);tax_rate=有 notax 时 `round(tax/notax*100,2)` 否则 DEFAULTS;summary=f"{vendor_name} {po_number or ''}".strip();head 的 department/revexp=body 首行;money=notax+tax。

- [ ] **Step 1: 写失败测试**(追加;fixtures 直接造镜像行+AP+accrual)

```python
from app.services.posting import emit_event


async def _mk_ap(db, *, source="epms", amount="100.00", tax="13.00",
                 number="AP-2026-0100", vendor="ACME Ltd", src_id=None):
    from app.models.ap_invoice import ApInvoice
    inv = ApInvoice(ap_invoice_number=number, source=source,
                    source_invoice_id=src_id or uuid.uuid4(),
                    vendor_name=vendor, amount=Decimal(amount),
                    tax_amount=Decimal(tax),
                    total_amount=Decimal(amount) + Decimal(tax),
                    currency="CAD", invoice_date=date(2026, 7, 10), status="posted")
    db.add(inv)
    await db.flush()
    return inv


async def _mk_accrual(db, ap, account="510102"):
    await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=ap.id, source_doc_number=ap.ap_invoice_number,
        event_type="accrual", prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": account,
                "debit": ap.amount, "currency": "CAD"},
               {"line_role": "sales_tax", "account_code": "1180",
                "debit": ap.tax_amount, "tax_code": "HST_ON", "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "220201",
                "credit": ap.total_amount, "currency": "CAD"}])


def test_account_full_path():
    from types import SimpleNamespace as NS
    from app.services.nc_ap_export import account_full_path
    coa = {"5101": NS(code="5101", name="Manufacturing Overhead", parent_code=None),
           "510102": NS(code="510102", name="Repairs", parent_code="5101")}
    assert account_full_path(coa, "510102") == "510102\\Manufacturing Overhead\\Repairs"
    assert account_full_path(coa, "9999") == "9999"


async def test_build_rows_epms_allocations(db_session):
    from app.models.mirrors import CostCenter, InvoicePoAllocation, PurchaseRequest, BudgetAccount
    from app.services.nc_ap_export import build_export_rows
    src = uuid.uuid4()
    po1, po2 = uuid.uuid4(), uuid.uuid4()
    cc = uuid.uuid4()
    db_session.add(CostCenter(id=cc, code="MOH-0106-E01", name="Engineering CC"))
    db_session.add(BudgetAccount(id=uuid.uuid4(), code="CRM004", name="Depreciation", is_active=True))
    db_session.add_all([
        PurchaseRequest(id=uuid.uuid4(), po_id=po1, cost_center_id=cc,
                        budget_code="CRM004", department_name="Engineering",
                        created_by=uuid.uuid4()),
        PurchaseRequest(id=uuid.uuid4(), po_id=po2, cost_center_id=None,
                        budget_code=None, department_name="Maintenance",
                        created_by=uuid.uuid4()),
        InvoicePoAllocation(id=uuid.uuid4(), invoice_id=src, po_id=po1,
                            allocated_amount=Decimal("60.00"), allocated_tax=Decimal("7.80")),
        InvoicePoAllocation(id=uuid.uuid4(), invoice_id=src, po_id=po2,
                            allocated_amount=Decimal("40.00"), allocated_tax=Decimal("5.20")),
    ])
    await db_session.flush()
    ap = await _mk_ap(db_session, src_id=src)
    await _mk_accrual(db_session, ap)

    heads, bodies, errors = await build_export_rows(db_session, [ap.id])
    assert errors == []
    assert len(heads) == 1 and len(bodies) == 2
    h = heads[0]
    assert h["seq"] == 0 and h["billno"] == "AP-2026-0100"
    assert h["department"] == "Engineering"          # first body row's dept
    b1 = next(b for b in bodies if b["notax"] == "60.00")
    assert b1["cost_center"] == "Engineering CC"
    assert b1["revexp"] == "Depreciation"
    assert b1["tax"] == "7.80" and b1["money"] == "67.80"
    assert b1["account_path"].startswith("510102")
    assert b1["tax_code"] == "001" and b1["tax_rate"] == "13.00"
    b2 = next(b for b in bodies if b["notax"] == "40.00")
    assert b2["cost_center"] == "" and b2["revexp"] == ""
    assert b2["department"] == "Maintenance"


async def test_build_rows_oa_pa_chain(db_session):
    from app.models.mirrors import CostCenter, ExpenseInvoice, User, BudgetAccount
    from app.models.pa import PaymentApplication
    from app.services.nc_ap_export import build_export_rows
    src, pa_id, cc, creator = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(CostCenter(id=cc, code="GA-0100", name="Admin CC"))
    db_session.add(BudgetAccount(id=uuid.uuid4(), code="CRM010", name="Office Supplies", is_active=True))
    db_session.add(User(id=creator, email="a@x.com", full_name="Alice Wong"))
    db_session.add(ExpenseInvoice(id=src, pa_id=pa_id))
    db_session.add(PaymentApplication(
        id=pa_id, pa_number="PA-1", title="t", pa_type="PA-DIR", status="paid",
        vendor_id=uuid.uuid4(), vendor_name="ACME Ltd",
        payment_amount=Decimal("113.00"), currency="CAD",
        cost_center_id=cc, budget_account_code="CRM010", created_by=creator))
    await db_session.flush()
    ap = await _mk_ap(db_session, source="oa", number="AP-2026-0101", src_id=src)
    await _mk_accrual(db_session, ap)

    heads, bodies, errors = await build_export_rows(db_session, [ap.id])
    assert errors == [] and len(bodies) == 1
    b = bodies[0]
    assert b["cost_center"] == "Admin CC" and b["revexp"] == "Office Supplies"
    assert b["employee"] == "Alice Wong"
    assert heads[0]["employee"] == "Alice Wong"


async def test_build_rows_errors(db_session):
    from app.services.nc_ap_export import build_export_rows
    ap_draft = await _mk_ap(db_session, number="AP-2026-0102")
    ap_draft.status = "draft"
    ap_noacc = await _mk_ap(db_session, number="AP-2026-0103")
    await db_session.flush()
    heads, bodies, errors = await build_export_rows(db_session, [ap_draft.id, ap_noacc.id])
    assert heads == [] and bodies == []
    reasons = {e["ap_number"]: e["reason"] for e in errors}
    assert reasons["AP-2026-0102"] == "status draft"
    assert reasons["AP-2026-0103"] == "no accrual posting"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py -q`
Expected: 新 4 个 FAIL(ModuleNotFoundError nc_ap_export),原 2 个 PASS

- [ ] **Step 3: 实现**(`app/services/nc_ap_export.py`,完整文件)

```python
"""NC AP export assembly — parallel-run: UniOps AP invoices -> NC payable-module
import rows (spec 2026-07-14-finance-nc-ap-export-design.md).

billno = UniOps AP number (the JV cross-check anchor). Dimension chains:
  EPMS: invoice_po_allocations (fallback invoices.po_id) -> purchase_requests
        head dims (CC / budget_code / department_name, 100% CC coverage)
  OA:   expense_invoices.pa_id -> payment_applications head dims
Expense account = the accrual posting's purchase_expense line (fallback account
granularity for now — line-level real accounts are a later project).
"""
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine
from app.models.coa import ChartOfAccount
from app.models.mirrors import (BudgetAccount, CostCenter, ExpenseInvoice,
                                Invoice, InvoicePoAllocation, PurchaseRequest, User)
from app.models.pa import PaymentApplication
from app.models.posting import PostingEvent, PostingLine

_ZERO = Decimal("0")

# Fixed enumeration defaults (user 2026-07-14: all fixed, config-by-constant).
NC_EXPORT_DEFAULTS = {
    "org": "Canada Royal Milk ULC",
    "ap_type": "Payable of Expense",
    "busi_process": "选择付款",
    "ap_type_code": "F1-Cxx-017",
    "buysell": "Domestic Purchases",
    "taxtype": "Tax Excluded",
    "tax_code": "001",
    "tax_rate": "13.00",
    "pay_term": "net 30 days",
    "tax_country": "Canada",
    "obj_type": "Supplier",
}
TAX_CODE_MAP = {"HST_ON": "001"}


def _s(v) -> str:
    return str(Decimal(v).quantize(Decimal("0.01")))


def account_full_path(coa: dict, code: str) -> str:
    """NC subjcode: 'code\\top-name\\...\\leaf-name' walking parent_code upward."""
    acct = coa.get(code)
    if acct is None:
        return code
    names, cur = [], acct
    while cur is not None:
        names.append(cur.name)
        cur = coa.get(cur.parent_code) if cur.parent_code else None
    return "\\".join([code] + list(reversed(names)))


async def _expense_account(db: AsyncSession, ap_id: uuid.UUID) -> str | None:
    """purchase_expense line's account on the AP's accrual posting; None = no accrual."""
    row = (await db.execute(
        select(PostingLine.account_code)
        .join(PostingEvent, PostingLine.event_id == PostingEvent.id)
        .where(PostingEvent.source_doc_type == "ap_invoice",
               PostingEvent.source_doc_id == ap_id,
               PostingEvent.event_type == "accrual",
               PostingLine.line_role == "purchase_expense")
        .limit(1))).scalar_one_or_none()
    return row


async def _lookup_map(db, model, ids, attr="id"):
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    col = getattr(model, attr)
    return {getattr(r, attr): r for r in (await db.execute(
        select(model).where(col.in_(ids)))).scalars()}


async def build_export_rows(db: AsyncSession, ap_ids: list) -> tuple[list, list, list]:
    """-> (heads, bodies, errors). Exportable = status not draft/void AND has an
    accrual posting; failures land in errors, the rest still export."""
    aps = (await db.execute(select(ApInvoice).where(ApInvoice.id.in_(ap_ids))
                            .order_by(ApInvoice.ap_invoice_number))).scalars().all()
    coa = {a.code: a for a in (await db.execute(select(ChartOfAccount))).scalars()}
    ccs = {c.id: c for c in (await db.execute(select(CostCenter))).scalars()}
    bas = {b.code: b for b in (await db.execute(select(BudgetAccount))).scalars()}
    d = NC_EXPORT_DEFAULTS

    heads, bodies, errors = [], [], []
    seq = 0
    for ap in aps:
        if ap.status in ("draft", "void"):
            errors.append({"ap_id": str(ap.id), "ap_number": ap.ap_invoice_number,
                           "reason": f"status {ap.status}"})
            continue
        acct_code = await _expense_account(db, ap.id)
        if acct_code is None:
            errors.append({"ap_id": str(ap.id), "ap_number": ap.ap_invoice_number,
                           "reason": "no accrual posting"})
            continue
        acct_path = account_full_path(coa, acct_code)

        tax_lines = (await db.execute(select(ApInvoiceTaxLine).where(
            ApInvoiceTaxLine.invoice_id == ap.id))).scalars().all()
        nc_tax_code = TAX_CODE_MAP.get(tax_lines[0].tax_code, d["tax_code"]) \
            if tax_lines and tax_lines[0].tax_code else d["tax_code"]

        # per-source dimension rows: [(notax, tax, cc_name, dept_name, revexp_name, employee)]
        rows: list[tuple] = []
        if ap.source == "epms":
            allocs = (await db.execute(select(InvoicePoAllocation).where(
                InvoicePoAllocation.invoice_id == ap.source_invoice_id))).scalars().all()
            po_ids = [a.po_id for a in allocs]
            if not allocs:
                inv = (await db.execute(select(Invoice).where(
                    Invoice.id == ap.source_invoice_id))).scalar_one_or_none()
                po_ids = [inv.po_id] if inv and inv.po_id else [None]
            prs = {}
            if any(po_ids):
                for pr in (await db.execute(select(PurchaseRequest).where(
                        PurchaseRequest.po_id.in_([p for p in po_ids if p])))).scalars():
                    prs[pr.po_id] = pr

            def _dims(po_id):
                pr = prs.get(po_id)
                if pr is None:
                    return ("", "", "", "")
                cc = ccs.get(pr.cost_center_id)
                ba = bas.get(pr.budget_code) if pr.budget_code else None
                return (cc.name if cc else "", pr.department_name or "",
                        ba.name if ba else "", "")

            if allocs:
                for a in allocs:
                    cc_n, dept_n, rev_n, emp = _dims(a.po_id)
                    rows.append((a.allocated_amount, a.allocated_tax,
                                 cc_n, dept_n, rev_n, emp))
            else:
                cc_n, dept_n, rev_n, emp = _dims(po_ids[0])
                rows.append((ap.amount, ap.tax_amount, cc_n, dept_n, rev_n, emp))
        else:  # oa — Direct PA head dims
            cc_n = dept_n = rev_n = emp = ""
            ei = (await db.execute(select(ExpenseInvoice).where(
                ExpenseInvoice.id == ap.source_invoice_id))).scalar_one_or_none()
            pa = None
            if ei is not None and ei.pa_id is not None:
                pa = (await db.execute(select(PaymentApplication).where(
                    PaymentApplication.id == ei.pa_id))).scalar_one_or_none()
            if pa is not None:
                cc = ccs.get(pa.cost_center_id)
                cc_n = cc.name if cc else ""
                dept_n = ""  # via CC's department below
                if cc is not None and cc.department_id:
                    dept = await _lookup_map(db, __import__("app.models.mirrors",
                                             fromlist=["Department"]).Department,
                                             [cc.department_id])
                    dept_n = next(iter(dept.values())).name if dept else ""
                ba = bas.get(pa.budget_account_code) if pa.budget_account_code else None
                rev_n = ba.name if ba else ""
                creator = await _lookup_map(db, User, [pa.created_by])
                emp = next(iter(creator.values())).full_name if creator else ""
            rows.append((ap.amount, ap.tax_amount, cc_n, dept_n, rev_n, emp))

        # tax proration when rows carry no tax but the AP does
        total_tax = sum((r[1] for r in rows), _ZERO)
        if total_tax == _ZERO and ap.tax_amount > _ZERO:
            base = sum((r[0] for r in rows), _ZERO)
            prorated, acc = [], _ZERO
            for i, r in enumerate(rows):
                t = (ap.tax_amount - acc if i == len(rows) - 1 else
                     (ap.tax_amount * r[0] / base).quantize(Decimal("0.01"))
                     if base > _ZERO else _ZERO)
                acc += t
                prorated.append((r[0], t, *r[2:]))
            rows = prorated

        first = rows[0]
        heads.append({
            "seq": seq, "billno": ap.ap_invoice_number,
            "ap_type": d["ap_type"], "busi_process": d["busi_process"],
            "billdate": ap.invoice_date.isoformat(), "busidate": ap.invoice_date.isoformat(),
            "obj_type": d["obj_type"], "supplier": ap.vendor_name or "",
            "department": first[3], "employee": first[5], "revexp": first[4],
            "currency": ap.currency, "ap_type_code": d["ap_type_code"],
            "tax_country": d["tax_country"],
        })
        for notax, tax, cc_n, dept_n, rev_n, emp in rows:
            notax_d, tax_d = Decimal(notax), Decimal(tax)
            rate = (_s(tax_d / notax_d * 100) if notax_d > _ZERO and tax_d > _ZERO
                    else d["tax_rate"])
            bodies.append({
                "seq": seq, "account_path": acct_path,
                "invoice_no": ap.vendor_invoice_number or ap.ap_invoice_number,
                "summary": f"{ap.vendor_name or ''} {ap.po_number or ''}".strip(),
                "pay_term": d["pay_term"], "obj_type": d["obj_type"],
                "supplier": ap.vendor_name or "", "department": dept_n,
                "cost_center": cc_n, "employee": emp, "revexp": rev_n,
                "currency": ap.currency, "rate": "1",
                "money": _s(notax_d + tax_d), "qty": "",
                "tax_code": nc_tax_code, "tax_rate": rate,
                "tax_price": "0.00000000", "notax": _s(notax_d), "tax": _s(tax_d),
                "taxtype": d["taxtype"], "department2": dept_n,
                "buysell": d["buysell"],
            })
        seq += 1
    return heads, bodies, errors
```

注:OA 分支里那个 `__import__` 写法**禁止**——直接把 `Department` 加进文件顶部的 mirrors import 并正常使用(计划书写疏漏,以此注为准):

```python
from app.models.mirrors import (BudgetAccount, CostCenter, Department, ExpenseInvoice,
                                Invoice, InvoicePoAllocation, PurchaseRequest, User)
...
                if cc is not None and cc.department_id:
                    dept = (await db.execute(select(Department).where(
                        Department.id == cc.department_id))).scalar_one_or_none()
                    dept_n = dept.name if dept else ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/services/nc_ap_export.py finance-api/tests/test_nc_ap_export.py
git commit -m "feat(finance): NC AP export assembly (EPMS allocation/PR dims, OA Direct-PA dims, tax proration)"
```

---

### Task 3: xlsx writer + 模板资产

**Files:**
- Create: `finance-api/assets/nc_ap_template.xlsx`(从 `C:/Project/uniops/AP_template.xlsx` 复制,二进制入库)
- Modify: `finance-api/app/services/nc_ap_export.py`(追加 write_xlsx)
- Modify: `finance-api/requirements.txt`(加 `openpyxl==3.1.5`)
- Test: `finance-api/tests/test_nc_ap_export.py`(追加)

**Interfaces:**
- Produces: `write_xlsx(heads: list[dict], bodies: list[dict]) -> bytes`。输出结构:R1 须知、R2 head 技术+列标(模板原样)、R3..R(2+N) head 数据(A=seq)、一空行、body 技术+列标行(模板 R5 原样)、body 数据行(A=seq);全部值写字符串。head 数据列顺序(B..Q)= billno 之前先 org:`org, billno, ap_type, busi_process, billdate, busidate, obj_type, supplier, department, employee, revexp, currency, ap_type_code, tax_country, "", ""`(def26/def14 空);body 数据列顺序(B..AD)= `account_path, invoice_no, summary, "", pay_term, obj_type, supplier, department, cost_center, employee, "", revexp, currency, rate, money, qty, tax_code, tax_rate, tax_price, notax, tax, taxtype, department2, "", "", "", buysell, "", ""`(material/project/def11/def7/def6/付款协议编码/税码主键 空)。

- [ ] **Step 1: 拷模板进 assets**

```bash
mkdir -p c:/Project/uniops/finance-api/assets
cp "C:/Project/uniops/AP_template.xlsx" c:/Project/uniops/finance-api/assets/nc_ap_template.xlsx
```

- [ ] **Step 2: 写失败测试**(追加)

```python
def test_write_xlsx_structure(tmp_path):
    import openpyxl
    from app.services.nc_ap_export import write_xlsx
    heads = [{"seq": 0, "billno": "AP-1", "ap_type": "Payable of Expense",
              "busi_process": "选择付款", "billdate": "2026-07-10",
              "busidate": "2026-07-10", "obj_type": "Supplier", "supplier": "ACME",
              "department": "Engineering", "employee": "", "revexp": "Depreciation",
              "currency": "CAD", "ap_type_code": "F1-Cxx-017", "tax_country": "Canada"},
             {"seq": 1, "billno": "AP-2", "ap_type": "Payable of Expense",
              "busi_process": "选择付款", "billdate": "2026-07-11",
              "busidate": "2026-07-11", "obj_type": "Supplier", "supplier": "Beta",
              "department": "", "employee": "Alice Wong", "revexp": "",
              "currency": "CAD", "ap_type_code": "F1-Cxx-017", "tax_country": "Canada"}]
    body_base = {"account_path": "510102\\MOH\\Repairs", "invoice_no": "INV-9",
                 "summary": "ACME PO-1", "pay_term": "net 30 days",
                 "obj_type": "Supplier", "supplier": "ACME", "department": "Engineering",
                 "cost_center": "Engineering CC", "employee": "", "revexp": "Depreciation",
                 "currency": "CAD", "rate": "1", "money": "67.80", "qty": "",
                 "tax_code": "001", "tax_rate": "13.00", "tax_price": "0.00000000",
                 "notax": "60.00", "tax": "7.80", "taxtype": "Tax Excluded",
                 "department2": "Engineering", "buysell": "Domestic Purchases"}
    bodies = [dict(body_base, seq=0), dict(body_base, seq=0, notax="40.00"),
              dict(body_base, seq=1)]
    data = write_xlsx(heads, bodies)
    p = tmp_path / "out.xlsx"
    p.write_bytes(data)
    ws = openpyxl.load_workbook(p)["Sheet1"]
    assert str(ws["A2"].value).startswith('"payablebill_$head')   # tech row kept
    assert ws["A3"].value == "0" and ws["C3"].value == "AP-1"     # head seq + billno
    assert ws["A4"].value == "1" and ws["C4"].value == "AP-2"
    assert ws.cell(5, 1).value in (None, "")                      # blank separator
    assert str(ws["A6"].value).startswith('"bodys')               # body tech row
    assert ws["A7"].value == "0" and ws["B7"].value.startswith("510102")
    assert ws["A9"].value == "1"                                  # 3rd body row -> doc 1
    assert ws["U7"].value == "60.00"                              # notax col
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && ./.venv/Scripts/python -m pip install openpyxl==3.1.5 -q && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py::test_write_xlsx_structure -q`
Expected: FAIL(ImportError write_xlsx)

- [ ] **Step 4: 实现**(追加到 nc_ap_export.py 末尾)

```python
# ── xlsx writer ────────────────────────────────────────────────────────────────────
import io
import os

_ASSET = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "assets", "nc_ap_template.xlsx")

_HEAD_COLS = ["org", "billno", "ap_type", "busi_process", "billdate", "busidate",
              "obj_type", "supplier", "department", "employee", "revexp",
              "currency", "ap_type_code", "tax_country", "", ""]
_BODY_COLS = ["account_path", "invoice_no", "summary", "", "pay_term", "obj_type",
              "supplier", "department", "cost_center", "employee", "", "revexp",
              "currency", "rate", "money", "qty", "tax_code", "tax_rate",
              "tax_price", "notax", "tax", "taxtype", "department2", "", "", "",
              "buysell", "", ""]


def write_xlsx(heads: list, bodies: list) -> bytes:
    """Rebuild the NC import layout on a template copy: notice + head tech row,
    head data rows (A=doc seq), one blank row, body tech row, body data rows.
    Everything written as text (NC requires all-text cells)."""
    import openpyxl
    wb = openpyxl.load_workbook(_ASSET)
    ws = wb["Sheet1"]

    # snapshot the two tech/label rows before clearing sample data
    body_tech = [ws.cell(5, c).value for c in range(1, ws.max_column + 1)]
    ws.delete_rows(3, ws.max_row - 2)   # drop head sample, blank, body tech, body sample

    r = 3
    for h in heads:
        ws.cell(r, 1, str(h["seq"]))
        for i, key in enumerate(_HEAD_COLS, start=2):
            ws.cell(r, i, "" if key == "" else str(h.get(key, "") if key != "org"
                    else NC_EXPORT_DEFAULTS["org"]))
        r += 1
    r += 1                               # blank separator row
    for c, v in enumerate(body_tech, start=1):
        if v is not None:
            ws.cell(r, c, v)
    r += 1
    for b in bodies:
        ws.cell(r, 1, str(b["seq"]))
        for i, key in enumerate(_BODY_COLS, start=2):
            ws.cell(r, i, "" if key == "" else str(b.get(key, "")))
        r += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

requirements.txt 末尾加一行 `openpyxl==3.1.5`。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py -q`
Expected: 7 passed(注意核对 head 列位:billno 在 C 列=第3列,因 B=org;测试断言 C3/C4)

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/assets/nc_ap_template.xlsx finance-api/app/services/nc_ap_export.py finance-api/requirements.txt finance-api/tests/test_nc_ap_export.py
git commit -m "feat(finance): NC AP export xlsx writer on template copy (hard NC import layout)"
```

---

### Task 4: API — 导出流 + 批次 + 标记

**Files:**
- Modify: `finance-api/app/api/v1/ap_invoices.py`(InvoiceOut 加 nc_exported_at;新增两端点)
- Test: `finance-api/tests/test_nc_ap_export.py`(追加)

**Interfaces:**
- Produces(前端依赖):
  - `POST /finance/v1/ap/nc-export` body `{ap_ids: [uuid]}`;权限 `_require_manage`(403);`build_export_rows` 有 errors → **409** `{"detail": {"errors": [...]}}`;成功 → xlsx 流(`media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"`,`Content-Disposition: attachment; filename=NC-AP-YYYYMMDD-HHMM.xlsx`),同时写 `nc_export_batches` 行并 UPDATE 各 AP 的 `nc_exported_at/nc_export_batch_id`。
  - `GET /finance/v1/ap/nc-export/batches` → 最近 20 批 `[{id, exported_at, exported_by, ap_count, filename}]`。
  - `InvoiceOut` 加 `nc_exported_at: datetime | None = None`。

- [ ] **Step 1: 写失败测试**(追加;client fixture 参照 test_nc_sync.py 的建法,_h 用 role='finance_manager')

```python
import pytest_asyncio
from datetime import timedelta
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings as app_settings
from app.db.base import get_db
from app.main import app


def _h(role="finance_manager"):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     app_settings.jwt_secret_key, algorithm=app_settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_export_endpoint_streams_and_marks(client, db_session):
    from app.models.nc_export import NcExportBatch
    ap = await _mk_ap(db_session, number="AP-2026-0200")
    await _mk_accrual(db_session, ap)
    r = await client.post("/finance/v1/ap/nc-export",
                          json={"ap_ids": [str(ap.id)]}, headers=_h())
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "NC-AP-" in r.headers["content-disposition"]
    assert len(r.content) > 1000
    await db_session.refresh(ap)
    assert ap.nc_exported_at is not None and ap.nc_export_batch_id is not None
    batch = (await db_session.execute(select(NcExportBatch))).scalars().first()
    assert batch is not None and batch.ap_count == 1


async def test_export_endpoint_guards(client, db_session):
    ap = await _mk_ap(db_session, number="AP-2026-0201")   # no accrual
    r = await client.post("/finance/v1/ap/nc-export",
                          json={"ap_ids": [str(ap.id)]}, headers=_h())
    assert r.status_code == 409
    assert r.json()["detail"]["errors"][0]["reason"] == "no accrual posting"
    r403 = await client.post("/finance/v1/ap/nc-export",
                             json={"ap_ids": [str(ap.id)]}, headers=_h(role="requester"))
    assert r403.status_code == 403
    rb = await client.get("/finance/v1/ap/nc-export/batches", headers=_h())
    assert rb.status_code == 200 and rb.json() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py -q`
Expected: 新 2 FAIL(404),原 7 PASS

- [ ] **Step 3: 实现** —— api/v1/ap_invoices.py:

(a) `InvoiceOut` 尾部加 `nc_exported_at: datetime | None = None`(`datetime` import 若缺补)。
(b) 文件末尾追加(import 区补 `Response` 或用 `fastapi.responses.Response`;`_require_manage` 从 app.api.v1.coa import;`datetime/timezone`):

```python
class NcExportIn(BaseModel):
    ap_ids: list[uuid.UUID]


@router.post("/nc-export")
async def nc_export(body: NcExportIn, user: CurrentUser,
                    db: AsyncSession = Depends(get_db)):
    """Generate the NC payable-module import xlsx for the chosen AP invoices,
    record a batch, and stamp the invoices. 409 lists non-exportable ones."""
    from fastapi.responses import Response
    from app.api.v1.coa import _require_manage
    from app.models.nc_export import NcExportBatch
    from app.services.nc_ap_export import build_export_rows, write_xlsx

    await _require_manage(db, user)
    heads, bodies, errors = await build_export_rows(db, body.ap_ids)
    if errors:
        raise HTTPException(status_code=409, detail={"errors": errors})
    if not heads:
        raise HTTPException(status_code=422, detail="no invoices to export")
    data = write_xlsx(heads, bodies)

    now = datetime.now(timezone.utc)
    fname = f"NC-AP-{now.strftime('%Y%m%d-%H%M')}.xlsx"
    batch = NcExportBatch(exported_by=uuid.UUID(user["sub"]), exported_at=now,
                          ap_count=len(heads), filename=fname)
    db.add(batch)
    await db.flush()
    for ap in (await db.execute(select(ApInvoice).where(
            ApInvoice.id.in_(body.ap_ids)))).scalars():
        ap.nc_exported_at = now
        ap.nc_export_batch_id = batch.id
    await db.commit()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/nc-export/batches")
async def nc_export_batches(_: CurrentUser, db: AsyncSession = Depends(get_db)):
    from app.models.nc_export import NcExportBatch
    rows = (await db.execute(select(NcExportBatch)
            .order_by(NcExportBatch.exported_at.desc()).limit(20))).scalars().all()
    return [{"id": str(b.id), "exported_at": b.exported_at.isoformat(),
             "exported_by": str(b.exported_by) if b.exported_by else None,
             "ap_count": b.ap_count, "filename": b.filename} for b in rows]
```

⚠️ 路由顺序:若该 router 有 `/{invoice_id}` 形式动态路由,`/nc-export*` 两个端点必须注册在其**之前**(检查文件现状,必要时移动位置)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py tests/test_ap_invoice.py -q`
Expected: 全 PASS(含既有 AP 套件回归)

- [ ] **Step 5: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/api/v1/ap_invoices.py finance-api/tests/test_nc_ap_export.py
git commit -m "feat(finance): /ap/nc-export endpoint — xlsx stream + batch audit + invoice stamping"
```

---

### Task 5: 前端 — AP 页复选 + Export to NC + Exported 列

**Files:**
- Modify: `finance/src/lib/api.ts`(POST 下载 helper)
- Modify: `finance/src/pages/finance/AccountsPayablePage.tsx`

**Interfaces:**
- Consumes: Task 4 端点;`InvoiceOut.nc_exported_at`。
- Produces: `financePostDownload(path: string, body: unknown): Promise<void>`(POST 收 blob 触发下载,从 Content-Disposition 取文件名;非 2xx 时按 financeRequest 同款错误提取,409 的 detail.errors 拼进 Error message)。

- [ ] **Step 1: api.ts 加 helper**(`financeDownload` 函数后):

```ts
/** POST JSON to Finance API and download the streamed file (Content-Disposition name). */
export async function financePostDownload(path: string, body: unknown): Promise<void> {
  const res = await fetch(`${FINANCE_API}/finance/v1${path}`, {
    method: 'POST', headers: authHeaders(true), body: JSON.stringify(body),
  })
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    const d = b.detail
    if (d && typeof d === 'object' && Array.isArray(d.errors)) {
      throw new Error(d.errors.map((e: any) => `${e.ap_number}: ${e.reason}`).join(' · '))
    }
    throw new Error(extractDetail(d, res.status))
  }
  const cd = res.headers.get('content-disposition') ?? ''
  const m = /filename="?([^";]+)"?/.exec(cd)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = m?.[1] ?? 'nc-export.xlsx'; a.click()
  URL.revokeObjectURL(url)
}
```

- [ ] **Step 2: AccountsPayablePage 改造**(锚点式修改):

(a) import:`financeApi, EPMS_URL, ...` 行加 `financePostDownload`;`useQuery` 行补 `useQueryClient`;lucide 加 `FileDown, Loader2`(Loader2 已有)。
(b) `ApInvoice` interface 加 `nc_exported_at: string | null`。
(c) 组件内 state 区(statusFilter 后)加:

```tsx
  const qc = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [exporting, setExporting] = useState(false)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const exportable = (inv: ApInvoice) => inv.status !== 'draft' && inv.status !== 'void'
  const toggle = (id: string) => setSelected((p) => {
    const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n
  })
  const runExport = async () => {
    const picked = invoices.filter((i) => selected.has(i.id))
    const already = picked.filter((i) => i.nc_exported_at).length
    if (already && !window.confirm(`${already} of ${picked.length} selected were already exported. Export again?`)) return
    setExporting(true); setBanner(null)
    try {
      await financePostDownload('/ap/nc-export', { ap_ids: [...selected] })
      setBanner({ kind: 'ok', text: `Exported ${picked.length} invoice${picked.length === 1 ? '' : 's'} to NC file.` })
      setSelected(new Set())
      qc.invalidateQueries({ queryKey: ['ap-invoices'] })
    } catch (e) {
      setBanner({ kind: 'err', text: (e as Error).message })
    } finally { setExporting(false) }
  }
```

(d) filters 行(两个 select 之后)加按钮:

```tsx
              <button onClick={runExport} disabled={exporting || selected.size === 0}
                      className="ml-auto flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50">
                {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileDown className="h-4 w-4" />}
                Export to NC{selected.size > 0 ? ` (${selected.size})` : ''}
              </button>
```

filters div 后(表格前)加 banner 渲染:

```tsx
            {banner && (
              <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
                banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
                {banner.text}
              </div>
            )}
```

(e) 表头行首插 `<th className="w-9 px-3 py-2" />`,尾插 `<th className="px-3 py-2 w-24">NC Export</th>`(colSpan 9→11 两处);数据行首插(阻断行点击):

```tsx
                        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                          {exportable(inv) && (
                            <input type="checkbox" checked={selected.has(inv.id)}
                                   onChange={() => toggle(inv.id)} />
                          )}
                        </td>
```

行尾插:

```tsx
                        <td className="px-3 py-2 text-xs text-neutral-500">
                          {inv.nc_exported_at ? inv.nc_exported_at.slice(0, 10) : '—'}
                        </td>
```

- [ ] **Step 3: Typecheck**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0(显式确认)

- [ ] **Step 4: Commit**

```bash
cd c:/Project/uniops
git add finance/src/lib/api.ts finance/src/pages/finance/AccountsPayablePage.tsx
git commit -m "feat(finance-ui): AP page Export to NC — row selection, download, exported marker"
```

---

### Task 6: dev 执行 + 实测(控制器亲自)

- [ ] **Step 1: 容器内迁移 + 重建**(requirements 变了要 --build)

```bash
cd c:/Project/uniops && docker compose -f docker-compose.dev.yml up -d --build finance-api
docker exec uniops_finance_api python -m alembic upgrade head
docker restart uniops_finance_api
```

- [ ] **Step 2: 真实导出冒烟**:挑 2-3 张真实 posted AP(混 EPMS/OA)调 POST /ap/nc-export 存盘;openpyxl 读回核字段;**用 SendUserFile 把生成的 xlsx 发给用户**,由用户人工核对格式并拿去 NC 试引入(终验)。

- [ ] **Step 3: 全量回归**:`pytest tests/test_nc_ap_export.py tests/test_ap_invoice.py tests/test_ap_accrual.py tests/test_ap_views.py -q` + 前端 typecheck。

---

## Self-Review 记录

- **Spec 覆盖**:§3.1 入口/批次/重导(T4/T5)、§3.2 枚举常量(T2)、§3.3 行重组+税摊(T2)、§3.4 两链维度(T2,镜像 T1)、§3.5 全路径(T2)、§3.6 head 取值(T2)、§4 模型(T1)、§5 导出器/写入/API(T2/T3/T4)、§6 UI(T5)、§7 验证(各任务+T6 实测/SendUserFile 终验)。
- **Placeholder 扫描**:通过(T2 Step3 中 `__import__` 反模式已用注释纠正为正常 import,实现者以纠正版为准)。
- **类型一致性**:heads/bodies dict 键(T2 定义,T3 `_HEAD_COLS/_BODY_COLS` 消费,T3 测试列位断言 billno=C 列)、errors 形状(T2→T4 409→T5 错误拼装)、`financePostDownload`(T5 内定义即消费)。
- **已知取舍**:head 的 org 列由 writer 从 NC_EXPORT_DEFAULTS 注入(不在 head dict);OA 单行、EPMS 无分摊单行;科目=accrual 兜底科目(spec 范围外声明);全路径拼法待 NC 引入实测校准(T6 终验点)。
