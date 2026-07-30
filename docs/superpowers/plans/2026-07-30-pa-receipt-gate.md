# PA 收货闸门 + 未收货催收货 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 非预付 PA 必须有 3-way matched 发票（`status=="matched"` 且已挂 GR）才能创建（硬拦 + 授权 override）；发票已匹配但未收货时主动催收货（每日重发邮件、深链进 New GR），GR 一建自动转 create_pa。

**Architecture:** 后端在 `api/v1/pa.py:create_pa` 加闸门（唯一权威），口径统一为"发票 3-way matched"（helper `po_has_three_way_matched_invoice`）。发票匹配后按是否已挂 GR 分流建 `create_pa` 或 `confirm_receipt` 任务；GR 创建时（`_autofill_gr_to_matched_invoices` 之后达成 3-way）关催办、补建 create_pa。每日重发复用现有 `daily_followup_loop`（零新机制），停发靠任务 complete。override 授权走 Access Control 矩阵新增权限 `pa_override_receipt`。

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic（epms-api）、identity seed_authz、React + Vite + TS（epms 前端）、pytest。

## Global Constraints

- **多会话纪律**：本任务单会话单分支单 worktree，不碰 main；发布时单点汇合。分支名 `feature/pa-receipt-gate`。
- **UI 文案全英文**（user-facing）；注释可中文。
- **迁移**：epms-api 新 alembic 的 `down_revision` 必须挂**真实链尾**（容器内 `alembic heads` 核实，勿按文件名猜；勿造双 head）。
- **测试库**：epms-api 测试覆盖 `POSTGRES_*` 到本地 docker `uniops_postgres`，库 `epms_test`；同刻只跑一个 epms 套件（互相 drop_all）。基线 epms 存量 failed 忽略，只看新增测试通过。
- **前端 tsc 门禁**：`cd epms && npx tsc -p tsconfig.app.json --noEmit`，对齐基线 59 条（不得净增）；epms 前端 TS 5.9.3 **不能**带 `--ignoreDeprecations`。
- **Decimal 序列化成字符串**：Pydantic 把 Decimal 发成 JSON 字符串，前端运算须 `Number()`。
- **权限**：override 用矩阵 `pa_override_receipt`，别硬编码角色。
- **3-way 定义（贯穿全程）**：`invoice.status == "matched"` 且 `invoice.gr_id IS NOT NULL`。GR **创建**即达成（不要求收货确认）。预付款 `pa_type=="prepayment"` 豁免闸门。

---

## File Structure

**epms-api（后端）**
- `app/crud/po.py` — 新增 `po_has_three_way_matched_invoice(db, po_id) -> bool`（3-way 真值 helper，唯一真相源）。
- `app/api/v1/pa.py` — `create_pa` 加收货闸门（422/403 + override 授权）。
- `app/crud/pa.py` — `create()` 落库 `receipt_override*`。
- `app/schemas/pa.py` — `PaCreate` 加 override 入参；`PaResponse` 加只读回显。
- `app/models/pa.py` — `PaymentApplication` 加 3 列。
- `alembic/versions/af_add_receipt_override_to_pa.py` — 迁移。
- `app/api/v1/invoices.py` — `_notify_requester_create_pa` → 改造为 `_on_invoice_matched` 分派器。
- `app/crud/gr.py` — `create()` 尾部调 `_on_three_way_reached`；重连 `_create_pa_task`。
- `app/crud/task.py` — `_backfill_create_pa_tasks` 收紧到 3-way。
- `app/crud/config.py` — `PERMISSION_KEYS` + 默认角色权限加 `pa_override_receipt`；email templates 加 `confirm_receipt`。
- `app/services/notification.py` — `_infer_template` 加 `confirm_receipt` 映射；`_task_link` 支持 confirm_receipt 深链 + `po_id` 模板变量。
- `app/models/task.py` — 注释补 `confirm_receipt` 类型（文档性）。

**identity-api**
- `scripts/seed_authz.py` — `MODULE_BY_KEY` + `DEFAULTS` 加 `pa_override_receipt`。

**epms 前端**
- `src/pages/pa/PaCreatePage.tsx` — 闸门 UI（阻断 + override 勾选/理由）。
- `src/pages/gr/GrCreatePage.tsx` — 已支持 `?poId=`，无需改（校验即可）。

---

## Task 0: 分支 + worktree（setup）

**Files:** 无代码改动。

- [ ] **Step 1: 建分支 + worktree**（遵循 R1/R2）

```bash
cd /c/Project/uniops
git worktree add -b feature/pa-receipt-gate ../uniops-pa-receipt-gate main
cd ../uniops-pa-receipt-gate
git status   # 确认干净、在新分支
```

- [ ] **Step 2: 把已写好的 spec 带过来（若不在该 worktree）**

spec 已在 `docs/superpowers/specs/2026-07-30-pa-receipt-gate-design.md`（main 树）。worktree 基于 main，故已包含。确认存在：

```bash
ls docs/superpowers/specs/2026-07-30-pa-receipt-gate-design.md
```

- [ ] **Step 3: Commit 计划文档**

```bash
git add docs/superpowers/plans/2026-07-30-pa-receipt-gate.md docs/superpowers/specs/2026-07-30-pa-receipt-gate-design.md
git commit -m "docs: PA receipt-gate spec + plan"
```

---

## Task 1: 新增矩阵权限 `pa_override_receipt`

**Files:**
- Modify: `epms-api/app/crud/config.py`（`PERMISSION_KEYS` ~L42、`_DEFAULT_ROLE_PERMISSIONS` ~L235）
- Modify: `identity-api/scripts/seed_authz.py`（`MODULE_BY_KEY` ~L14、`DEFAULTS` ~L59）
- Modify: `epms-api/tests/conftest.py`（`_MODULE_BY_KEY` ~L34、`_DEFAULTS` ~L80 —— 测试用的矩阵**第三份拷贝**，`_restore_default_matrix` fixture 用它给测试库播种；不同步则 Task 4 里 finance 角色在测试中拿不到 `pa_override_receipt`，override 用例会误 403）
- Test: `epms-api/tests/test_admin.py`

**Interfaces:**
- Produces: 权限键 `"pa_override_receipt"` 出现在 `PERMISSION_KEYS`；默认对 `finance_bp/finance_manager/cfo/procurement_officer/procurement_manager/system_admin` = True。`build_scope(...)["perms"]["pa_override_receipt"]` 后续任务消费。

- [ ] **Step 1: Write the failing test**（epms-api/tests/test_admin.py 末尾追加）

```python
def test_pa_override_receipt_permission_registered():
    from app.crud.config import PERMISSION_KEYS, _DEFAULT_ROLE_PERMISSIONS
    assert "pa_override_receipt" in PERMISSION_KEYS
    # 授权角色默认有,requester 默认无
    assert _DEFAULT_ROLE_PERMISSIONS["finance_manager"]["pa_override_receipt"] is True
    assert _DEFAULT_ROLE_PERMISSIONS["procurement_officer"]["pa_override_receipt"] is True
    assert _DEFAULT_ROLE_PERMISSIONS["requester"]["pa_override_receipt"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd epms-api && python -m pytest tests/test_admin.py::test_pa_override_receipt_permission_registered -v
```
Expected: FAIL（KeyError / assert False，因键未注册）。

- [ ] **Step 3: 加权限键（epms-api/app/crud/config.py）**

在 `PERMISSION_KEYS` 列表 `"data_maintenance",` 之后加一行：

```python
    "data_maintenance",
    # 无收货 override:允许在无 3-way matched 发票时强制建 PA(带理由,落 PA 审计)。
    "pa_override_receipt",
```

在 `_DEFAULT_ROLE_PERMISSIONS` 里给授权角色补该权限（`_P(...)` 用 kwargs，直接加 `pa_override_receipt=True`）：

```python
    "procurement_officer":  _P(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "procurement_manager":  _P(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    ...
    "finance_bp":           _P(create_gr=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_manager":      _P(create_pr=True, create_gr=True, admin_panel=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    ...
    "cfo":                  _P(pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
```
（`system_admin` 已 `{k: True for k}` 全 True，无需改。）

- [ ] **Step 4: 同步 identity 种子（identity-api/scripts/seed_authz.py）**

`MODULE_BY_KEY` 里 `"data_maintenance": "epms",` 后加：

```python
    "parts_catalog": "epms", "admin_panel": "epms", "data_maintenance": "epms",
    "pa_override_receipt": "epms",
```

`DEFAULTS` 里对应角色补 `pa_override_receipt=True`（与 epms 侧一致：procurement_officer / procurement_manager / finance_bp / finance_manager / cfo）：

```python
    "procurement_officer": _p(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "procurement_manager": _p(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "finance_bp":          _p(create_gr=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_manager":     _p(create_pr=True, create_gr=True, admin_panel=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "cfo":                 _p(pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
```

- [ ] **Step 4b: 同步测试矩阵拷贝（epms-api/tests/conftest.py）**

conftest 有一份独立的矩阵拷贝供测试库播种。`_MODULE_BY_KEY` 里 `"data_maintenance": "epms",` 后加 `"pa_override_receipt": "epms",`；`_DEFAULTS` 里对授权角色补 `pa_override_receipt=True`（与 config.py/seed_authz 完全一致：procurement_officer / procurement_manager / finance_bp / finance_manager / cfo；system_admin 已全 True）：

```python
    "procurement_officer": _p(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "procurement_manager": _p(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "finance_bp":          _p(create_gr=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_manager":     _p(create_pr=True, create_gr=True, admin_panel=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "cfo":                 _p(pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd epms-api && python -m pytest tests/test_admin.py::test_pa_override_receipt_permission_registered -v
```
Expected: PASS。（另可加一条断言 `_DEFAULTS["finance_manager"]["pa_override_receipt"] is True` 的 conftest 一致性测试，非必需。）

- [ ] **Step 6: 部署待办记录（不写代码，写进 plan 备注即可）**

⚠️ 生产部署后需在 identity 容器重跑种子让 stored 矩阵含新键，并核实 `uniops_authz.effective_permissions` 对授权角色返回 `pa_override_receipt=True`：
```
docker exec uniops_identity_api python -m scripts.seed_authz
```
（`compute_effective` 用 `DEFAULTS[k]` 兜底，未 stored 也应为默认值；仍需实测确认包不裸奔。）

- [ ] **Step 7: Commit**

```bash
git add epms-api/app/crud/config.py identity-api/scripts/seed_authz.py epms-api/tests/conftest.py epms-api/tests/test_admin.py
git commit -m "feat(authz): add pa_override_receipt matrix permission"
```

---

## Task 2: 迁移 + 模型 + schema —— PA 加 receipt_override 列

**Files:**
- Modify: `epms-api/app/models/pa.py`
- Create: `epms-api/alembic/versions/af_add_receipt_override_to_pa.py`
- Modify: `epms-api/app/schemas/pa.py`（`PaCreate` L45、`PaResponse` L97）
- Verify-only: `epms-api/tests/test_pa.py`（跑现有用例确认模型不破坏建表；功能测试在 Task 4 写）

**Interfaces:**
- Produces: `PaymentApplication.receipt_override: bool`、`.receipt_override_reason: str|None`、`.receipt_override_by: uuid|None`。`PaCreate.receipt_override: bool=False`、`.receipt_override_reason: str|None=None`。`PaResponse` 回显三字段。

- [ ] **Step 1: 核实 alembic 真实链尾**

```bash
cd epms-api && python -m alembic heads
```
记下唯一 head（已实测唯一 head = `ae_add_approved_at_to_pa`，2026-07-30；若你执行时 heads 有变/分叉，以实测为准并相应改 down_revision）。

- [ ] **Step 2: （本任务不写功能测试）**

落库/回显的功能测试 `test_create_pa_persists_receipt_override` 需要 3-way PO 与 finance 客户端 fixture（Task 4 建），故**在 Task 4 编写并运行**，不在本任务。本任务是结构性改动（model + migration + schema），验证见 Step 6（`alembic heads` 单头 + schema 导入 + 现有 `test_pa.py` 仍绿）。**不要**在本任务往 `test_pa.py` 加引用未定义 fixture 的测试，否则 collection 会报错。

- [ ] **Step 3: 模型加列（app/models/pa.py）**

在 `PaymentApplication` 合适位置（其它审计列附近）加：

```python
    # 无收货 override(见 PA receipt gate):非预付 PA 在无 3-way matched 发票时,
    # 授权角色可带理由强制创建。三列纯审计。
    receipt_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    receipt_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_override_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```
（确认文件顶部已 import `Boolean, Text` 及 `UUID`；缺则补 import。）

- [ ] **Step 4: 写迁移（alembic/versions/af_add_receipt_override_to_pa.py）**

```python
"""add receipt_override columns to payment_applications

Revision ID: af_add_receipt_override_to_pa
Revises: ae_add_approved_at_to_pa
Create Date: 2026-07-30
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "af_add_receipt_override_to_pa"
down_revision = "ae_add_approved_at_to_pa"   # 实测唯一 head(2026-07-30)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_applications",
        sa.Column("receipt_override", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("payment_applications",
        sa.Column("receipt_override_reason", sa.Text(), nullable=True))
    op.add_column("payment_applications",
        sa.Column("receipt_override_by", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_applications", "receipt_override_by")
    op.drop_column("payment_applications", "receipt_override_reason")
    op.drop_column("payment_applications", "receipt_override")
```

- [ ] **Step 5: schema 加字段（app/schemas/pa.py）**

`PaCreate` 末尾（L64 `line_items` 后）加：

```python
    receipt_override: bool = False
    receipt_override_reason: str | None = Field(default=None, max_length=500)
```

`PaResponse` 加（与其它字段并列）：

```python
    receipt_override: bool = False
    receipt_override_reason: str | None = None
    receipt_override_by: uuid.UUID | None = None
```

- [ ] **Step 6: 验证（不触碰共享本地 dev 库）**

```bash
cd epms-api
# (a) 迁移链完整:加了 af 后仍是唯一 head = af,承接 ae
python -m alembic heads          # 期望仅: af_add_receipt_override_to_pa (head)
python -m alembic history -r ae_add_approved_at_to_pa:af_add_receipt_override_to_pa
# (b) schema 字段可导入
python -c "from app.schemas.pa import PaCreate, PaResponse; print('receipt_override' in PaCreate.model_fields, 'receipt_override_by' in PaResponse.model_fields)"
# (c) 模型新列不破坏建表:现有 PA 套件仍绿(conftest 用 Base.metadata create_all 建 epms_test)
python -m pytest tests/test_pa.py -q
```
Expected：(a) 唯一 head 为 `af_...`，history 显示 ae→af 链接；(b) 打印 `True True`；(c) test_pa.py 无新增 FAIL（对基线）。
**不要**跑 `alembic upgrade head`——本地 dev `epms` 库是共享的，勿改它；迁移只需保证链正确、DDL 与模型一致（真实库在发布时由 migrate-prod.sh 应用）。
`test_create_pa_persists_receipt_override` 依赖 Task 4 fixture，本任务不跑（Task 4 覆盖）。

- [ ] **Step 7: Commit**

```bash
git add epms-api/app/models/pa.py epms-api/alembic/versions/af_add_receipt_override_to_pa.py epms-api/app/schemas/pa.py
git commit -m "feat(pa): add receipt_override columns + schema fields + migration af"
```

---

## Task 3: 3-way 真值 helper

**Files:**
- Modify: `epms-api/app/crud/po.py`
- Create: `epms-api/tests/test_three_way_helper.py`（自包含直接 DB 构造，范本 = `tests/test_gr_invoice_backfill.py`；**不**依赖不存在的 fixture）

**Interfaces:**
- Produces: `async def po_has_three_way_matched_invoice(db: AsyncSession, po_id: uuid.UUID) -> bool` —— PO 有任一 `Invoice.status=="matched"` 且 `Invoice.gr_id IS NOT NULL` 时返回 True。被 Task 4/6/7/8 消费。

- [ ] **Step 1: Write the failing test**（新建 tests/test_three_way_helper.py —— 自包含，套用 `test_gr_invoice_backfill.py` 的 `async with sm.AsyncSessionLocal() as db` + 直接建模式，**绕开 approval-api**）

```python
"""po_has_three_way_matched_invoice: True 仅当 matched 发票已挂 gr_id。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest

import app.db.session as sm
from app.crud import user as user_crud
from app.crud.po import po_has_three_way_matched_invoice
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


def _invoice(po_id, vendor_id, uploaded_by, *, ref, status, gr_id=None):
    return Invoice(
        internal_ref=ref, vendor_invoice_number=ref, vendor_id=vendor_id,
        vendor_name="Acme", amount=Decimal("100"), tax_amount=Decimal("0"),
        total_amount=Decimal("100"), invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1), status=status, line_items=[],
        po_id=po_id, gr_id=gr_id, uploaded_by=uploaded_by,
    )


async def _make_po(db):
    user = await user_crud.create(db, RegisterRequest(
        email=f"tw-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="TW Tester", role="warehouse_staff",
    ))
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme",
                    category="supplier", contact_name="C", contact_email="c@x.com")
    db.add(vendor)
    await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                       vendor_id=vendor.id, vendor_name="Acme", status="issued",
                       created_by=user.id)
    db.add(po)
    await db.flush()
    return po, vendor, user


@pytest.mark.asyncio
async def test_three_way_true_only_when_matched_and_gr_linked():
    async with sm.AsyncSessionLocal() as db:
        po, vendor, user = await _make_po(db)
        inv = _invoice(po.id, vendor.id, user.id, ref=f"I-{uuid.uuid4().hex[:6]}",
                       status="matched", gr_id=None)
        db.add(inv)
        await db.flush()
        # matched 但无 GR → 非 3-way
        assert await po_has_three_way_matched_invoice(db, po.id) is False
        # 挂上 GR(gr_id) → 3-way
        inv.gr_id = uuid.uuid4()
        await db.flush()
        assert await po_has_three_way_matched_invoice(db, po.id) is True


@pytest.mark.asyncio
async def test_three_way_false_for_exception_status():
    async with sm.AsyncSessionLocal() as db:
        po, vendor, user = await _make_po(db)
        db.add(_invoice(po.id, vendor.id, user.id, ref=f"I-{uuid.uuid4().hex[:6]}",
                        status="exception", gr_id=uuid.uuid4()))
        await db.flush()
        assert await po_has_three_way_matched_invoice(db, po.id) is False
```

> 关键：`_invoice` 必须显式设 `gr_id`（`test_gr_invoice_backfill.py` 里只设 `gr_ids`，那对本 helper 不算数）。helper 查 `gr_id` 标量。

- [ ] **Step 2: Run test to verify it fails**

```bash
cd epms-api && python -m pytest tests/test_three_way_helper.py -v
```
Expected: FAIL（ImportError: cannot import name `po_has_three_way_matched_invoice`）。

- [ ] **Step 3: 实现 helper（app/crud/po.py 末尾）**

```python
from app.models.invoice import Invoice   # 确认已 import,缺则补


async def po_has_three_way_matched_invoice(db: AsyncSession, po_id: uuid.UUID) -> bool:
    """True 当 PO 有任一张 3-way matched 发票:status=='matched' 且已挂 GR(gr_id 非空)。

    这是 PA 收货闸门 / create_pa 触发 / confirm_receipt 停发 的统一真相源。
    GR 一创建即由 _autofill_gr_to_matched_invoices 写 gr_id → 立即达成 3-way,
    不要求收货确认(collected/confirmed)。
    """
    row = (await db.execute(
        select(Invoice.id).where(
            Invoice.po_id == po_id,
            Invoice.status == "matched",
            Invoice.gr_id.is_not(None),
        ).limit(1)
    )).scalar_one_or_none()
    return row is not None
```
（确认 `po.py` 顶部已 `from sqlalchemy import select`。）

- [ ] **Step 4: Run test to verify it passes**

```bash
cd epms-api && python -m pytest tests/test_three_way_helper.py -v
```
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/crud/po.py epms-api/tests/test_three_way_helper.py
git commit -m "feat(po): add po_has_three_way_matched_invoice helper"
```

---

## Task 4: PA create 收货闸门（后端唯一权威）

**Files:**
- Modify: `epms-api/app/api/v1/pa.py`（`create_pa` L70-）
- Modify: `epms-api/app/crud/pa.py`（`create()` L145-）
- Test: `epms-api/tests/test_pa.py`

**Interfaces:**
- Consumes: `po_has_three_way_matched_invoice`（Task 3）、`build_scope(...)["perms"]["pa_override_receipt"]`（Task 1）、`PaCreate.receipt_override*`（Task 2）。
- Produces: 闸门行为 —— 非预付且无 3-way 发票时：无 override→422；有 override 但无权限→403；有 override 无理由→422；合法 override→201 且落库。

> **测试环境铁律（务必读）**：`tests/conftest.py` 的测试矩阵**只种 17 个 matrix key，没种 `epms.pa.write`**；`require_permission("epms.pa.write")` 只有 **system_admin 短路通过**。故测试里 **只有 `admin_client`（system_admin）能 POST /pa**。所有闸门用例都用 `admin_client`。system_admin 同时持 `pa_override_receipt`，但闸门先看 `body.receipt_override` **标志**——admin 不带标志一样 422。
> **3-way 状态直接建库**：套用 `tests/test_three_way_helper.py` 里已跑通的 `_invoice`（显式设 `gr_id`）+ `_make_gr`（真实 GR 行，满足 `invoice.gr_id` 的 FK）+ `tests/test_gr_invoice_backfill.py` 的建模式。**跨 session 必须 `await db.commit()`**（HTTP 端点用另一个 session，只 flush 看不到；参照 `test_list_excludes_oa_direct_pas`）。

- [ ] **Step 1: 建两个 PO 构造 helper（tests/test_pa.py 顶部）**

- `_make_bare_po(admin_client, vendor_id)` = 现有 `_make_po`（PO via API，无发票，即"无 3-way"）。直接复用，别新建。
- `_make_three_way_po(admin_client, test_engine, vendor_id)`：先 `_make_po` 建 PO，再用 `async with async_sessionmaker(test_engine, ...)() as db:` 直接插一张 `status="matched"` 且 `gr_id=<真实 GR.id>` 的 Invoice（GR 用真实行，FK 到 goods_receipts）；**`await db.commit()`**。返回 po dict。GR/Invoice 的必填列与 `created_by`/`uploaded_by`（若非空）照 `test_three_way_helper.py`/`test_gr_invoice_backfill.py` 填（可 `user_crud.create` 一个 warehouse_staff 用户做 created_by/uploaded_by）。

- [ ] **Step 2: Write the failing tests**（全部 `admin_client`；用现有 `_make_vendor`/`_pa_payload`/`_make_prepayment`）

```python
@pytest.mark.asyncio
async def test_regular_pa_blocked_without_three_way(admin_client):
    v = await _make_vendor(admin_client, "VND-GATE-BLOCK")
    po = await _make_bare_po(admin_client, v["id"])          # matched 发票都没有 → 无 3-way
    r = await admin_client.post(PA_URL, json=_pa_payload(po["id"]))   # 不带 override 标志
    assert r.status_code == 422, r.text
    assert "goods receipt" in r.json()["detail"].lower() or "3-way" in r.json()["detail"]


@pytest.mark.asyncio
async def test_regular_pa_allowed_with_three_way(admin_client, test_engine):
    v = await _make_vendor(admin_client, "VND-GATE-OK")
    po = await _make_three_way_po(admin_client, test_engine, v["id"])
    r = await admin_client.post(PA_URL, json=_pa_payload(po["id"]))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_prepayment_pa_exempt_from_gate(admin_client):
    v = await _make_vendor(admin_client, "VND-GATE-PREPAY")
    po = await _make_bare_po(admin_client, v["id"])          # 无 3-way
    r = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], pa_type="prepayment", prepayment_pct="50",
        expected_settlement_date="2026-05-01", subtotal="100.00", tax_amount="0.00"))
    assert r.status_code == 201, r.text                       # 预付豁免


@pytest.mark.asyncio
async def test_override_with_permission_persists(admin_client):
    v = await _make_vendor(admin_client, "VND-GATE-OVR")
    po = await _make_bare_po(admin_client, v["id"])          # 无 3-way,但 admin 有 pa_override_receipt
    r = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], receipt_override=True, receipt_override_reason="urgent freight in transit"))
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["receipt_override"] is True
    assert data["receipt_override_reason"] == "urgent freight in transit"
    assert data["receipt_override_by"] is not None


@pytest.mark.asyncio
async def test_override_requires_reason(admin_client):
    v = await _make_vendor(admin_client, "VND-GATE-NOREASON")
    po = await _make_bare_po(admin_client, v["id"])
    r = await admin_client.post(PA_URL, json=_pa_payload(
        po["id"], receipt_override=True, receipt_override_reason="   "))   # 空白理由
    assert r.status_code == 422, r.text
    assert "reason" in r.json()["detail"].lower()
```

> **省略 `test_override_requires_permission`（403 分支）**：本测试环境只有 system_admin 能过 `epms.pa.write`，而 system_admin 恒有 `pa_override_receipt`——**没有"有 pa.write 但无 override"的角色可用**，无法干净触发该 403。该分支逻辑简单（`if not scope["perms"].get("pa_override_receipt"): 403`），交由代码审查覆盖；在 report 里注明此覆盖缺口。**不要**为它硬造 seed（会牵动矩阵测试基建）。

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd epms-api && python -m pytest tests/test_pa.py -k "gate or three_way or override or exempt" -v
```
Expected: FAIL（当前无闸门，422/持久化都不触发）。

- [ ] **Step 4: 加闸门（app/api/v1/pa.py，create_pa 内，prepayment/settlement guard 之后、`pa_crud.create()` 之前）**

先在函数早处取 scope（若尚未取）：

```python
    scope = await build_scope(db, user)
```
（`build_scope` 已 import；`user` 为 JWT payload dict，含 `sub`/`role`。）

在 settlement/balance guard 块之后插入：

```python
    # ── 收货闸门 —— 预付款先付后收豁免;其余类型须有 3-way matched 发票 ──
    if body.pa_type != "prepayment":
        if not await po_crud.po_has_three_way_matched_invoice(db, body.po_id):
            if not body.receipt_override:
                raise HTTPException(
                    status_code=422,
                    detail="No 3-way matched invoice for this PO (a matched invoice "
                           "with a linked goods receipt). Create a goods receipt "
                           "first, or override with a reason.",
                )
            if not scope["perms"].get("pa_override_receipt", False):
                raise HTTPException(
                    status_code=403,
                    detail="You are not authorized to create a payment without goods receipt.",
                )
            if not (body.receipt_override_reason or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail="A reason is required to override the goods-receipt requirement.",
                )
```

- [ ] **Step 5: 落库 override（app/crud/pa.py，create() 内 PaymentApplication(...) 构造处）**

给 `pa_crud.create()` 增加透传参数（签名加 `receipt_override: bool = False`, `receipt_override_reason: str | None = None`, `receipt_override_by: uuid.UUID | None = None`），并在 `PaymentApplication(...)` 里赋值：

```python
        receipt_override=receipt_override,
        receipt_override_reason=(receipt_override_reason or None) if receipt_override else None,
        receipt_override_by=receipt_override_by if receipt_override else None,
```

在 `api/v1/pa.py` 调用 `pa_crud.create(...)` 处补传：

```python
        receipt_override=body.receipt_override,
        receipt_override_reason=body.receipt_override_reason,
        receipt_override_by=uuid.UUID(user["sub"]) if body.receipt_override else None,
```

- [ ] **Step 5b: 修复被闸门打破的现有 settlement 测试**

新闸门把 `settlement` 也纳入（非预付），故现有**期望 201** 的 4 个 settlement 测试会在无 3-way 的 PO 上变 422。它们不是测闸门、且用 `admin_client`（持 override 权限），给这 4 处 `_pa_payload(...)` 加 `receipt_override=True, receipt_override_reason="settlement test setup"`：
  - `test_settlement_payment_amount_is_net_of_prepayment`
  - `test_settlement_balance_marks_prepayment_settled`
  - `test_settlement_net_zero_exact_auto_reconciles`
  - `test_settlement_net_zero_overpaid_needs_confirmation`

（`test_settlement_applied_exceeds_invoice_rejected` 与 `test_settlement_requires_valid_prepayment_ref` 期望 422、且在**闸门之前**的 settlement guard 触发，**不用改**。`_make_approved_po` 那 8 个用例是 approval-api 存量失败，**不改**。）

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd epms-api && python -m pytest tests/test_pa.py -k "gate or three_way or override or exempt" -v
```
Expected: 5 个新用例全 PASS。

- [ ] **Step 7: 回归 —— 对基线核对失败集**

```bash
cd epms-api && python -m pytest tests/test_pa.py -v 2>&1 | tail -25
```
Expected：新 5 用例 PASS；4 个 settlement 用例（已补 override）仍 PASS；**失败集恰为基线那 8 个 `_make_approved_po`（approval-api）**，无新增。若某 settlement 用例变 422，说明 Step 5b 漏补。

- [ ] **Step 8: Commit**

```bash
git add epms-api/app/api/v1/pa.py epms-api/app/crud/pa.py epms-api/tests/test_pa.py
git commit -m "feat(pa): enforce 3-way matched invoice gate on PA create with authorized override"
```

---

## Task 5: confirm_receipt 邮件模板 + 类型映射 + New GR 深链

**Files:**
- Modify: `epms-api/app/crud/config.py`（`_DEFAULT_EMAIL_TEMPLATES` 字典）
- Modify: `epms-api/app/services/notification.py`（`_infer_template` L355、`_task_link` L37、base_vars L222）
- Test: `epms-api/tests/test_notification_dispatch.py`

**Interfaces:**
- Produces: 模板键 `confirm_receipt`；`_infer_template("confirm_receipt", False) == "confirm_receipt"`；`_task_link("po", id, task_type="confirm_receipt")` → `{EPMS_URL}/gr/new?poId={id}`；base_vars 含 `po_id`。

- [ ] **Step 1: Write the failing test**（tests/test_notification_dispatch.py 追加）

```python
def test_confirm_receipt_template_and_link():
    from app.services.notification import _infer_template, _task_link
    import uuid
    pid = uuid.uuid4()
    assert _infer_template("confirm_receipt", is_followup=False) == "confirm_receipt"
    link = _task_link("po", pid, task_type="confirm_receipt")
    assert link.endswith(f"/gr/new?poId={pid}")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd epms-api && python -m pytest tests/test_notification_dispatch.py::test_confirm_receipt_template_and_link -v
```
Expected: FAIL（`_task_link` 无 `task_type` 参数 / 映射缺失）。

- [ ] **Step 3: `_task_link` 支持 task_type（notification.py L37）**

```python
def _task_link(document_type: str, document_id: Any, task_type: str | None = None) -> str:
    # confirm_receipt:引导收货人直达 New GR 页并预填 PO(前端已支持 ?poId=)。
    if task_type == "confirm_receipt":
        return f"{settings.EPMS_URL}/gr/new?poId={document_id}"
    dt = (document_type or "").lower()
    routes: dict[str, tuple[str, str]] = {
        ...  # 原样保留
    }
    base, path = routes.get(dt, (settings.OA_URL, "\expenses\{id}"))
    return f"{base}{path.format(id=document_id)}"
```

在调用处（L220）改为传 task.type：

```python
    link = _task_link(task.document_type, task.document_id, task_type=task.type)
```

在 base_vars（L222）加 `po_id`（供专属模板内联深链，容错非 po 文档）：

```python
        "po_id": str(task.document_id) if task.document_type == "po" else "",
```

- [ ] **Step 4: `_infer_template` 加映射（notification.py L359 的 dict）**

```python
        "create_pa": "create_pa_reminder",
        "confirm_receipt": "confirm_receipt",
```

- [ ] **Step 5: 加邮件模板（config.py 的 templates 字典，`create_pa_reminder` 附近）**

```python
    "confirm_receipt": _DEFAULT_EMAIL_TEMPLATE(
        "Invoice received for {po_number} — please confirm goods receipt",
        "Hi {recipient_name},\n\nInvoice <b>{invoice_number}</b> from {vendor} has been matched to "
        "PO <b>{po_number}</b>, but the goods/service has not been received yet. "
        "Please confirm receipt and create a Goods Receipt.\n\n"
        "<a href=\"{link}\">Create Goods Receipt</a>\n\n{company_name}",
    ),
```
（`{link}` 对 confirm_receipt 已由 Step 3 指向 `/gr/new?poId=`。）

- [ ] **Step 6: Run test to verify it passes**

```bash
cd epms-api && python -m pytest tests/test_notification_dispatch.py::test_confirm_receipt_template_and_link -v
```
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add epms-api/app/services/notification.py epms-api/app/crud/config.py epms-api/tests/test_notification_dispatch.py
git commit -m "feat(notify): confirm_receipt template + New-GR deep link"
```

---

## Task 6: 发票匹配分派器（3-way→create_pa；否则→confirm_receipt）

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py`（`_notify_requester_create_pa` L79 改为 `_on_invoice_matched`；调用点 L317、L545）
- Test: `epms-api/tests/test_invoice_*`（新增或既有匹配测试文件）

**Interfaces:**
- Consumes: `po_has_three_way_matched_invoice`（Task 3）、`is_physical`（schemas.gr）、`fire_and_forget_notify`。
- Produces: `async def _on_invoice_matched(db, invoice)` —— 发票 match 后：本发票已挂 GR 且 status matched → 建/re-notify `create_pa`；否则 → 建/re-notify `confirm_receipt`（物理 role=warehouse_staff/user=None；服务 role=requester/user=PR.created_by），锚 PO，type="confirm_receipt"。每 PO 一条 confirm_receipt 去重。

- [ ] **Step 1: Write the failing tests**

```python
async def test_matched_invoice_without_gr_creates_confirm_receipt_physical(...):
    # 物理 PO,发票 matched 但 gr_id=None
    # → 建 confirm_receipt(assigned_role='warehouse_staff', assigned_user_id=None, document_type='po')
    # → 不建 create_pa
    ...

async def test_matched_invoice_without_gr_creates_confirm_receipt_service(...):
    # 服务 PO(type 4) → confirm_receipt(assigned_role='requester', assigned_user_id=PR.created_by)
    ...

async def test_matched_invoice_with_gr_creates_create_pa(...):
    # 发票 matched 且 gr_id 非空 → 建 create_pa,不建 confirm_receipt
    ...
```
（用既有 invoice-match 测试的构造方式；断言查 `tasks` 表 type/assigned_role/assigned_user_id。）

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd epms-api && python -m pytest tests/ -k "confirm_receipt and (physical or service or create_pa)" -v
```
Expected: FAIL。

- [ ] **Step 3: 改造分派器（app/api/v1/invoices.py）**

把 `_notify_requester_create_pa` 重命名为 `_on_invoice_matched` 并改 body：

```python
async def _on_invoice_matched(db, invoice) -> None:
    """发票 match 后按是否达成 3-way 分流:
    - 已挂 GR(gr_id 非空) → create_pa 任务(Requester)
    - 未挂 GR → confirm_receipt 催收货(物理→warehouse_staff 池 / 服务→Requester)
    """
    from app.schemas.gr import is_physical
    if not invoice.po_id:
        return
    po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == invoice.po_id))).scalar_one_or_none()
    if po is None:
        return
    pr = None
    if po.pr_id:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == po.pr_id))).scalar_one_or_none()

    three_way = invoice.status == "matched" and invoice.gr_id is not None
    if three_way:
        await _create_or_renotify_create_pa(db, po, pr, invoice)   # = 原 create_pa 逻辑抽出
    else:
        await _create_or_renotify_confirm_receipt(db, po, pr, invoice, is_physical(po.type))
```

`_create_or_renotify_create_pa` = 把原函数 L104-140 的去重/建 create_pa/notify 逻辑原样搬入（assigned_role="requester", assigned_user_id=pr.created_by if pr else None）。

新增 `_create_or_renotify_confirm_receipt`：

```python
async def _create_or_renotify_confirm_receipt(db, po, pr, invoice, physical: bool) -> None:
    existing = (await db.execute(select(Task).where(
        Task.type == "confirm_receipt",
        Task.document_type == "po",
        Task.document_id == po.id,
        Task.is_completed.is_(False),
    ))).scalar_one_or_none()
    if existing is not None:
        fire_and_forget_notify(existing, db, extra_vars={"invoice_number": invoice.internal_ref})
        return
    if physical:
        assigned_role, assigned_user_id = "warehouse_staff", None
    else:
        assigned_role, assigned_user_id = "requester", (pr.created_by if pr else None)
    task = Task(
        type="confirm_receipt", priority="normal",
        document_type="po", document_id=po.id, document_number=po.number,
        assigned_role=assigned_role, assigned_user_id=assigned_user_id,
        title=f"Confirm goods receipt for {po.number}",
        description=(f"Invoice {invoice.internal_ref} has been matched to PO {po.number} "
                     f"but goods/service is not received yet. Please confirm receipt and create a GR."),
        vendor=po.vendor_name, amount=po.total,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    fire_and_forget_notify(task, db, extra_vars={"invoice_number": invoice.internal_ref})
```

改两处调用点（L317、L545）`await _notify_requester_create_pa(db, result)` → `await _on_invoice_matched(db, result)`。

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd epms-api && python -m pytest tests/ -k "confirm_receipt and (physical or service or create_pa)" -v
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/api/v1/invoices.py epms-api/tests/
git commit -m "feat(invoice): dispatch create_pa vs confirm_receipt on match by 3-way state"
```

---

## Task 7: GR 创建交接（达成 3-way → 关催办 + 建 create_pa）

**Files:**
- Modify: `epms-api/app/crud/gr.py`（`create()` 尾部；`_create_pa_task` 重连）
- Test: `epms-api/tests/test_gr.py` 或 `tests/test_gr_invoice_backfill.py`

**Interfaces:**
- Consumes: `po_has_three_way_matched_invoice`（Task 3）、`_autofill_gr_to_matched_invoices`（已存在，先跑）。
- Produces: `async def _on_three_way_reached(db, po_id)` —— 关闭 PO 所有未完成 `confirm_receipt`；若 PO 现有 3-way 发票且无 PA 且无未完成 create_pa → 建 create_pa。`gr.create()` 在 `_autofill_gr_to_matched_invoices` 之后调用它。

- [ ] **Step 1: Write the failing test**

```python
async def test_gr_creation_closes_confirm_receipt_and_creates_create_pa(...):
    # 先有 matched 发票(gr_id=None) + 一条 confirm_receipt(open)
    # 建 GR(覆盖该发票的 PO 行) → _autofill 写 gr_id → 3-way
    # 断言:confirm_receipt.is_completed == True; 出现一条 open create_pa(type='create_pa', document_type='po')
    ...
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd epms-api && python -m pytest tests/test_gr.py -k "closes_confirm_receipt" -v
```
Expected: FAIL。

- [ ] **Step 3: 加交接函数 + 在 create() 里调用（app/crud/gr.py）**

在 `create()` 中 `await _autofill_gr_to_matched_invoices(db, gr, lines)` 之后加：

```python
    if gr.po_id is not None:
        await _on_three_way_reached(db, gr.po_id)
```

新增：

```python
async def _on_three_way_reached(db: AsyncSession, po_id: uuid.UUID) -> None:
    """GR 创建使 PO 达成 3-way 后:关掉催收货提醒,补建 create_pa(若尚无 PA/任务)。"""
    from app.crud.po import po_has_three_way_matched_invoice
    # 1) 关闭 confirm_receipt
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(Task).where(
        Task.type == "confirm_receipt",
        Task.document_type == "po",
        Task.document_id == po_id,
        Task.is_completed.is_(False),
    ))).scalars().all()
    for t in rows:
        t.is_completed = True
        t.completed_at = now
    # 2) 若已 3-way 且无 PA 且无 open create_pa → 建 create_pa
    if not await po_has_three_way_matched_invoice(db, po_id):
        return
    from app.models.pa import PaymentApplication
    has_pa = (await db.execute(select(PaymentApplication.id).where(
        PaymentApplication.po_id == po_id).limit(1))).scalar_one_or_none()
    if has_pa is not None:
        return
    has_task = (await db.execute(select(Task.id).where(
        Task.type == "create_pa", Task.document_type == "po",
        Task.document_id == po_id, Task.is_completed.is_(False)).limit(1))).scalar_one_or_none()
    if has_task is not None:
        return
    po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one_or_none()
    if po is None:
        return
    requester_id = await get_pr_requester_id(db, po.pr_id)
    db.add(Task(
        type="create_pa", priority="normal",
        document_type="po", document_id=po_id, document_number=po.number,
        assigned_role="requester", assigned_user_id=requester_id,
        title=f"Create Payment Application for {po.number}",
        description=f"Goods received for PO {po.number}. Please create a Payment Application.",
        vendor=po.vendor_name, amount=po.total,
    ))
    await db.flush()
```
（`_create_pa_task`（旧死代码）可删或保留；本任务用内联建单，锚 PO 与 invoice 路径一致。若删除需确认无其它引用。）

- [ ] **Step 4: Run test to verify it passes**

```bash
cd epms-api && python -m pytest tests/test_gr.py -k "closes_confirm_receipt" -v
```
Expected: PASS。

- [ ] **Step 5: 回归 GR 套件**

```bash
cd epms-api && python -m pytest tests/test_gr.py tests/test_gr_invoice_backfill.py -v
```
Expected: 无新增 FAIL。

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/crud/gr.py epms-api/tests/
git commit -m "feat(gr): on GR creation, close confirm_receipt and raise create_pa (3-way handoff)"
```

---

## Task 8: backfill 收紧到 3-way

**Files:**
- Modify: `epms-api/app/crud/task.py`（`_backfill_create_pa_tasks` L272）
- Test: `epms-api/tests/`（backfill 相关）

**Interfaces:**
- Consumes: `Invoice.gr_id`。
- Produces: 回填候选发票条件加 `Invoice.gr_id IS NOT NULL`（仅 3-way），不再给"仅 2-way"PO 造 create_pa。

- [ ] **Step 1: Write the failing test**

```python
async def test_backfill_skips_two_way_only_po(...):
    # matched 但 gr_id=None 的发票 + payable PO + 无 PA → backfill 不应建 create_pa
    ...
async def test_backfill_creates_for_three_way_po(...):
    # matched 且 gr_id 非空 → backfill 建 create_pa
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd epms-api && python -m pytest tests/ -k "backfill and (two_way or three_way)" -v
```
Expected: FAIL（当前只按 status==matched 回填）。

- [ ] **Step 3: 收紧候选（app/crud/task.py，`recent_matched_pos` 子查询）**

```python
    recent_matched_pos = select(Invoice.po_id).where(
        Invoice.status == "matched",
        Invoice.po_id.is_not(None),
        Invoice.gr_id.is_not(None),               # ← 仅 3-way
        Invoice.created_at >= _BACKFILL_MIN_CREATED,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd epms-api && python -m pytest tests/ -k "backfill and (two_way or three_way)" -v
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/crud/task.py epms-api/tests/
git commit -m "fix(task): backfill create_pa only for 3-way matched POs"
```

---

## Task 9: 前端 Create PA 闸门 UI

**Files:**
- Modify: `epms/src/pages/pa/PaCreatePage.tsx`
- 验证：`epms/src/pages/gr/GrCreatePage.tsx`（已支持 `?poId=`，仅校验）

**Interfaces:**
- Consumes: 后端 422/403（无 3-way / 无权限）、`PaResponse.receipt_override*`、用户权限 `pa_override_receipt`（前端权限来源沿用现有 usePermissions/authz 钩子）。
- Produces: 非预付 + 无 3-way 时阻断 Submit；授权用户可勾 Override + 填理由后提交，payload 带 `receipt_override`/`receipt_override_reason`。

- [ ] **Step 1: 定位现有结构**

```bash
cd epms && sed -n '1,80p' src/pages/pa/PaCreatePage.tsx   # 看 PO 拉取/发票关联/submit payload/权限钩子
```
确认：如何取当前 PO 的发票与 GR 关联（判断 3-way = 存在 matched 且挂 GR 的发票）、submit 的 body 组装处、如何读当前用户权限。

- [ ] **Step 2: 计算 3-way 状态 + 权限**

在组件内基于已拉取的 PO 发票列表算：

```tsx
const hasThreeWay = poInvoices.some(
  (inv) => inv.status === 'matched' && !!inv.gr_id,
)
const isPrepayment = paType === 'prepayment'
const canOverride = perms?.pa_override_receipt === true   // 沿用现有权限钩子
const receiptBlocked = !isPrepayment && !hasThreeWay
```

- [ ] **Step 3: 阻断提示 + Override UI（英文文案）**

```tsx
{receiptBlocked && (
  <div className="rounded-md border border-warning-300 bg-warning-50 p-3 text-sm">
    <p className="font-medium text-warning-800">No goods receipt linked to a matched invoice yet.</p>
    <p className="text-warning-700">
      A Payment Application normally requires goods to be received. Create a Goods Receipt first.
    </p>
    {canOverride && (
      <label className="mt-2 flex items-start gap-2">
        <input type="checkbox" checked={receiptOverride}
               onChange={(e) => setReceiptOverride(e.target.checked)} />
        <span>Override — proceed without goods receipt (a reason is required).</span>
      </label>
    )}
    {canOverride && receiptOverride && (
      <textarea className="mt-2 w-full rounded border p-2" rows={2}
        placeholder="Reason for override" value={receiptOverrideReason}
        onChange={(e) => setReceiptOverrideReason(e.target.value)} />
    )}
  </div>
)}
```

state：`const [receiptOverride, setReceiptOverride] = useState(false)` / `const [receiptOverrideReason, setReceiptOverrideReason] = useState('')`。

- [ ] **Step 4: Submit 禁用条件 + payload**

Submit 禁用：

```tsx
const submitDisabled = /* 既有条件 */ ||
  (receiptBlocked && (!canOverride || !receiptOverride || !receiptOverrideReason.trim()))
```

payload 增补：

```tsx
  receipt_override: receiptBlocked && receiptOverride,
  receipt_override_reason: receiptBlocked && receiptOverride ? receiptOverrideReason.trim() : null,
```

- [ ] **Step 5: tsc 门禁**

```bash
cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | tail -5
# 统计错误数,须 ≤ 基线 59(不得净增)
```
Expected: 错误数不超过基线。

- [ ] **Step 6: 手动冒烟（browse 或本地）**

- 无 3-way 的 PO：requester 打开 Create PA → 见阻断、Submit 禁用、无 override 勾选框。
- 授权用户（finance）：见 override 勾选 + 理由，勾上填理由后可提交 → 201。
- prepayment：不受阻断影响。
- confirm_receipt 邮件里的链接点开 → New GR 页且 PO 预填。

- [ ] **Step 7: Commit**

```bash
git add epms/src/pages/pa/PaCreatePage.tsx
git commit -m "feat(epms-web): PA create receipt gate UI with authorized override"
```

---

## Task 10: 端到端回归 + 收尾

**Files:** 无新代码；跑全套 + 文档。

- [ ] **Step 1: 后端全量（串行，测试库唯一）**

```bash
cd epms-api && python -m pytest tests/test_pa.py tests/test_gr.py tests/test_three_way_helper.py tests/test_notification_dispatch.py tests/test_admin.py -v
```
Expected: 本任务新增用例全 PASS；对基线无新增 FAIL。

- [ ] **Step 2: 前端 tsc**

```bash
cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"
```
Expected: ≤ 59。

- [ ] **Step 3: 更新 spec 的"待落实"项**

回填 spec §8.3 深链已由 Task 5 落实（`_task_link` task_type 分支）；§7 部署后重跑 seed_authz 已在 Task 1 Step 6 记录。

- [ ] **Step 4: Commit + 汇报**

```bash
git add -A && git commit -m "test: PA receipt-gate end-to-end regression green"
```
向用户汇报：分支 `feature/pa-receipt-gate` 就绪、测试结果、**有迁移 af**（发布须跑 `migrate-prod.sh`）、**部署后重跑 identity seed_authz**。等用户决定合并/发布（遵循 R4 单点汇合）。

---

## Self-Review

**Spec 覆盖核对：**
- §3 D1 3-way 口径 → Task 3 helper + Task 4 gate ✓
- §3 D2/D3 硬拦+矩阵 override → Task 1（权限）+ Task 4（422/403/reason）✓
- §3 D4 pa_type 范围（prepayment 豁免）→ Task 4 `if body.pa_type != "prepayment"` ✓
- §6 迁移+schema → Task 2 ✓
- §8.1 发票匹配分派 → Task 6 ✓
- §8.2 GR 创建交接 → Task 7 ✓
- §8.3 每日重发（复用 daily loop，无新机制）→ 无需任务；停发靠 Task 7 complete confirm_receipt ✓
- §8.4 backfill 收紧 → Task 8 ✓
- §8.5 模板 + 类型映射 + 深链 → Task 5 ✓
- §9 前端 → Task 9 ✓

**占位符扫描：** 无 TBD/TODO 式空步骤；每步含真实代码或命令。前端测试以 tsc + 手动冒烟替代单测（epms 前端无组件单测惯例，符合既有实践）。

**类型一致性：** `po_has_three_way_matched_invoice`（Task 3 定义，Task 4/7/8 消费）签名一致；`_on_invoice_matched`（Task 6）替换两处 `_notify_requester_create_pa` 调用；`receipt_override*` 字段名跨 model/schema/crud/前端一致；`_task_link(..., task_type=)`（Task 5 定义）在同任务内更新唯一调用点。

**已知需执行时确认（非占位，均带核实步骤）：**
- alembic 真实 head（Task 2 Step 1）。
- `test_pa.py` 既有 fixture 命名（Task 4 Step 1，按实际调整 helper 名）。
- `uniops_authz.effective_permissions` 是否需重跑 seed 才返回新键（Task 1 Step 6）。
