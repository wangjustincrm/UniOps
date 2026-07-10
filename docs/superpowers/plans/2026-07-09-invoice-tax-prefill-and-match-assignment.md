# Invoice Tax Prefill + Match Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 发票创建时按有效税率自动预填 tax line;AP 可将发票的 PO 匹配工作指派给任何用户(邮件提醒),被指派人 match 出非零偏差时由发起指派的 AP 复核。

**Architecture:** Feature 1 是 epms-api 服务端纯后端改动(创建钩子 + mdm 税码反推,fail-open)。Feature 2 复用现有 Task+通知体系:`match_invoice`/`review_match` 两种任务、按任务放宽 match 授权与可见性、新发票状态 `match_review`、前端三处指派入口 + 复核面板。

**Tech Stack:** FastAPI + SQLAlchemy async + alembic(epms-api);React + TanStack Query(epms);pytest(本地 docker postgres)。

**Spec:** `docs/superpowers/specs/2026-07-09-invoice-tax-prefill-and-match-assignment-design.md`

## Global Constraints

- **执行环境**:在独立 worktree 执行(主工作区有并行会话且处于 detached HEAD)。`git worktree add C:/Project/uniops-feat-invoice origin/main -b feature/invoice-tax-and-match-assign`,全部改动和 commit 都在该 worktree。
- **交付**:feature 分支上按任务提交;最终经用户同意后以 **两个 squash commit** 合入 main(Task 1-2 → commit 1;Task 3-9 → commit 2,含 spec/plan 文档),再走标准 13 镜像发布。
- **UI 文案纯英文**(用户既定约定);代码注释可中文。
- **epms-api 测试命令**(每次都要带 env,PowerShell 变量不跨调用持久):
  `cd C:/Project/uniops-feat-invoice/epms-api; $env:POSTGRES_HOST="localhost"; $env:POSTGRES_PASSWORD="7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"; ..\..\uniops\epms-api\.venv\Scripts\python.exe -m pytest <targets> -q`
  (worktree 无 .venv,复用主仓 `C:/Project/uniops/epms-api/.venv`;本地 docker `uniops_postgres` 已有 `epms_test` 库)
- **存量失败**:`tests/test_invoices.py` 有 6 个 `POST /po/{id}/action` 404 的基线失败,与本计划无关,回归只看"无新增失败"。
- **前端 typecheck**:`cd epms; node ../node_modules/typescript/bin/tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`,存量错误多,只看两点:本计划改动文件零错误 + 无新增错误。
- **Decimal 走 JSON 是字符串**,前端运算前 `Number()`。
- **自定义下拉浮层必须 createPortal 到 body**(既定坑);本计划的用户选择器放在 modal 内(modal 本身已 portal)。

---

### Task 1: mdm 税码获取 + 税码反推纯函数(Feature 1 前半)

**Files:**
- Modify: `epms-api/app/services/mdm_client.py`(加 `get_tax_codes`)
- Create: `epms-api/app/services/tax_prefill.py`(`pick_tax_code` 纯函数,本 task 只写它)
- Test: `epms-api/tests/test_tax_prefill.py`

**Interfaces:**
- Produces: `pick_tax_code(amount: Decimal, tax_amount: Decimal, codes: list[dict]) -> dict | None`
  — codes 元素形如 `{"code": "HST-ON", "rate": "0.13", "recoverable": True, ...}`(mdm `/tax/codes` 响应);唯一容差命中返回该 dict,否则 None
- Produces: `MdmClient.get_tax_codes() -> list[dict] | None`

- [ ] **Step 1: 写失败测试**

```python
# epms-api/tests/test_tax_prefill.py
"""Tax-line auto-prefill — pure inference tests (no DB, no network)."""
from decimal import Decimal

from app.services.tax_prefill import pick_tax_code

CODES = [
    {"code": "HST-ON", "rate": "0.13", "recoverable": True},
    {"code": "GST", "rate": "0.05", "recoverable": True},
    {"code": "PST-BC", "rate": "0.07", "recoverable": False},
]


def test_pick_exact_rate_match():
    # 4.81 / 36.99 = 13.0035% → HST-ON (±0.5pp)
    assert pick_tax_code(Decimal("36.99"), Decimal("4.81"), CODES)["code"] == "HST-ON"


def test_pick_no_match_returns_none():
    # 10% falls in no code's tolerance band
    assert pick_tax_code(Decimal("100"), Decimal("10"), CODES) is None


def test_pick_ambiguous_returns_none():
    # two codes within tolerance of 5.2% (GST 5% and a hypothetical 5.4%)
    codes = CODES + [{"code": "X-54", "rate": "0.054", "recoverable": True}]
    assert pick_tax_code(Decimal("100"), Decimal("5.2"), codes) is None


def test_pick_zero_tax_returns_none():
    assert pick_tax_code(Decimal("100"), Decimal("0"), CODES) is None
    assert pick_tax_code(Decimal("0"), Decimal("5"), CODES) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_tax_prefill.py -q`(带 Global Constraints 的 env 前缀)
Expected: `ModuleNotFoundError`/`ImportError`(tax_prefill 不存在)

- [ ] **Step 3: 最小实现**

```python
# epms-api/app/services/tax_prefill.py
"""发票创建时按有效税率反推税码,自动预填单条 tax line(fail-open)。

规则(spec 2026-07-09):有效率 = tax_amount/amount,在激活税码中找
|rate - 有效率| ≤ 0.005(±0.5 个百分点)的候选;唯一命中才预填,
零命中/并列一律跳过(照旧进 ITC uncoded 清单,宁缺勿错)。
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

_RATE_TOLERANCE = Decimal("0.005")


def pick_tax_code(amount: Decimal, tax_amount: Decimal, codes: list[dict]) -> dict | None:
    if amount is None or tax_amount is None or amount <= 0 or tax_amount <= 0:
        return None
    effective = Decimal(tax_amount) / Decimal(amount)
    hits = [
        c for c in codes
        if abs(Decimal(str(c.get("rate", "0"))) - effective) <= _RATE_TOLERANCE
    ]
    return hits[0] if len(hits) == 1 else None
```

`mdm_client.py` 在 `get_supplier` 后追加:

```python
    async def get_tax_codes(self) -> list | None:
        """B2 税码主数据(激活集)。"""
        return await self._get("/tax/codes")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_tax_prefill.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/services/tax_prefill.py epms-api/app/services/mdm_client.py epms-api/tests/test_tax_prefill.py
git commit -m "feat(epms-api): tax-code inference by effective rate (pure function)"
```

---

### Task 2: 创建时预填接线(Feature 1 后半)

**Files:**
- Modify: `epms-api/app/services/tax_prefill.py`(加 `apply_tax_prefill`)
- Modify: `epms-api/app/api/v1/invoices.py`(`upload_invoice` 接线,~L124-133)
- Test: `epms-api/tests/test_tax_prefill.py`(追加端点级测试)

**Interfaces:**
- Consumes: Task 1 的 `pick_tax_code`、`MdmClient.get_tax_codes`
- Produces: `apply_tax_prefill(db, invoice, bearer_token) -> None`(永不 raise)

- [ ] **Step 1: 写失败测试(追加到 test_tax_prefill.py)**

```python
import pytest

INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "Tax Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


def _inv(vendor_id, amount, tax):
    return {
        "vendor_id": vendor_id, "vendor_invoice_number": f"TAXPRE-{amount}-{tax}",
        "amount": amount, "tax_amount": tax, "currency": "CAD",
        "invoice_date": "2026-07-09", "due_date": "2026-08-08",
    }


class _FakeMdm:
    """Stands in for MdmClient — returns canned tax codes."""
    def __init__(self, codes):
        self._codes = codes

    def __call__(self, bearer_token):  # constructor signature parity
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get_tax_codes(self):
        if isinstance(self._codes, Exception):
            raise self._codes
        return self._codes


HST = [{"code": "HST-ON", "rate": "0.13", "recoverable": True},
       {"code": "GST", "rate": "0.05", "recoverable": True}]


@pytest.mark.asyncio
async def test_create_prefills_tax_line_on_unique_match(admin_client, monkeypatch):
    from app.services import tax_prefill
    monkeypatch.setattr(tax_prefill, "MdmClient", _FakeMdm(HST))
    v = await _make_vendor(admin_client, "VND-TAXPRE-01")
    inv = (await admin_client.post(INV_URL, json=_inv(v["id"], "100.00", "13.00"))).json()

    r = await admin_client.get(f"{INV_URL}/{inv['id']}/tax-lines")
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert len(lines) == 1
    assert lines[0]["tax_code"] == "HST-ON"
    assert lines[0]["taxable_amount"] == "100.00"
    assert lines[0]["tax_amount"] == "13.00"      # 保留 header 原值,不重算
    assert lines[0]["recoverable"] is True


@pytest.mark.asyncio
async def test_create_skips_prefill_when_no_rate_match(admin_client, monkeypatch):
    from app.services import tax_prefill
    monkeypatch.setattr(tax_prefill, "MdmClient", _FakeMdm(HST))
    v = await _make_vendor(admin_client, "VND-TAXPRE-02")
    inv = (await admin_client.post(INV_URL, json=_inv(v["id"], "100.00", "9.00"))).json()
    assert (await admin_client.get(f"{INV_URL}/{inv['id']}/tax-lines")).json()["lines"] == []


@pytest.mark.asyncio
async def test_create_survives_mdm_failure(admin_client, monkeypatch):
    from app.services import tax_prefill
    monkeypatch.setattr(tax_prefill, "MdmClient", _FakeMdm(RuntimeError("mdm down")))
    v = await _make_vendor(admin_client, "VND-TAXPRE-03")
    r = await admin_client.post(INV_URL, json=_inv(v["id"], "100.00", "13.00"))
    assert r.status_code == 201                    # fail-open:发票照常创建
    assert (await admin_client.get(f"{INV_URL}/{r.json()['id']}/tax-lines")).json()["lines"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_tax_prefill.py -q`
Expected: 新 3 个 FAIL(`AttributeError: ... has no attribute 'MdmClient'` 或 lines==[] 断言失败),原 4 个 PASS

- [ ] **Step 3: 实现**

`tax_prefill.py` 追加:

```python
from app.services.mdm_client import MdmClient  # 顶部 import(monkeypatch 锚点)


async def apply_tax_prefill(db, invoice, bearer_token: str) -> None:
    """创建后自动预填 — 任何失败只记 warning,绝不影响发票创建。"""
    from app.models.invoice_tax_line import InvoiceTaxLine
    try:
        if not invoice.tax_amount or invoice.tax_amount <= 0 or not invoice.amount or invoice.amount <= 0:
            return
        async with MdmClient(bearer_token) as mdm:
            codes = await mdm.get_tax_codes()
        pick = pick_tax_code(invoice.amount, invoice.tax_amount, codes or [])
        if pick is None:
            logger.info("tax prefill: no unique rate match for invoice %s", invoice.id)
            return
        db.add(InvoiceTaxLine(
            invoice_id=invoice.id, line_no=1,
            tax_code=pick["code"], taxable_amount=invoice.amount,
            tax_amount=invoice.tax_amount,
            recoverable=bool(pick.get("recoverable", True)),
        ))
        await db.flush()
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.warning("tax prefill skipped for invoice %s: %s", getattr(invoice, "id", "?"), exc)
```

`invoices.py` `upload_invoice` 在 `create` 与 `finance_sync` 之间接线(顺序重要:先建 tax line 再 sync,finance 才能带上税行):

```python
    inv = await invoice_crud.create(
        db, body, vendor_name=vendor.name, uploaded_by=uuid.UUID(user["sub"])
    )
    await tax_prefill.apply_tax_prefill(db, inv, token)   # 新增
    await finance_sync.sync_ap_invoice(db, inv, token)
    return inv
```

顶部加 `from app.services import tax_prefill`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_tax_prefill.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/services/tax_prefill.py epms-api/app/api/v1/invoices.py epms-api/tests/test_tax_prefill.py
git commit -m "feat(epms-api): auto-prefill invoice tax line at create by effective-rate inference"
```

---

### Task 3: tasks.created_by 迁移(Feature 2 基础)

**Files:**
- Create: `epms-api/alembic/versions/z3_add_created_by_to_tasks.py`
- Modify: `epms-api/app/models/task.py`
- Test: 无独立测试(后续 task 的测试覆盖);迁移用 alembic 升降验证

**Interfaces:**
- Produces: `Task.created_by: uuid.UUID | None`(指派人/任务发起人;复核任务路由依赖它)

- [ ] **Step 1: 写迁移**

```python
# epms-api/alembic/versions/z3_add_created_by_to_tasks.py
"""tasks.created_by — 记录任务发起人(match 指派的 assigner,复核任务路由用)

Revision ID: z3
Revises: z2
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "z3"
down_revision = "z2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column(
        "created_by", UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("tasks", "created_by")
```

- [ ] **Step 2: model 加字段**(`task.py` 的 `completed_by` 之后)

```python
    # 任务发起人(如 match 指派的 AP);历史任务为 NULL
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
```

- [ ] **Step 3: 本地 dev 库验证升降**

```bash
cd C:/Project/uniops-feat-invoice/epms-api
POSTGRES_HOST=localhost POSTGRES_PASSWORD=<同上> \
  C:/Project/uniops/epms-api/.venv/Scripts/python.exe -m alembic upgrade head
# 确认: docker exec uniops_postgres psql -U epms -d epms -c "\d tasks" | grep created_by
```

Expected: 列存在;`alembic downgrade -1` + 再 `upgrade head` 均成功。
测试库无需手动处理(conftest 用 Base.metadata.create_all,自动带新列)。

- [ ] **Step 4: Commit**

```bash
git add epms-api/alembic/versions/z3_add_created_by_to_tasks.py epms-api/app/models/task.py
git commit -m "feat(epms-api): tasks.created_by column (migration z3)"
```

---

### Task 4: assign-match 端点 + 通知

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py`(新端点)
- Modify: `epms-api/app/schemas/invoice.py`(AssignMatchRequest)
- Modify: `epms-api/app/services/notification.py`(`_infer_template` 两条映射 + invoice 链接复数修正)
- Test: `epms-api/tests/test_invoice_assign.py`(新文件)

**Interfaces:**
- Consumes: Task 3 的 `Task.created_by`
- Produces: `POST /invoices/{id}/assign-match {user_id}` → 200 InvoiceResponse;开放 `match_invoice` 任务(document_type="invoice", assigned_role="assigned", assigned_user_id=被指派人, created_by=AP)
- Produces(测试复用):`tests/test_invoice_assign.py` 中的 `_setup_invoice(admin_client)` helper 返回 `(vendor, po, invoice)`

- [ ] **Step 1: 写失败测试**

```python
# epms-api/tests/test_invoice_assign.py
"""Match-PO 指派:端点、改派、通知任务。"""
import uuid

import pytest
from sqlalchemy import select

INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"
PO_URL = "/api/v1/po"


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "Assign Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_po(client, vendor_id, lines):
    po = await client.post(PO_URL, json={
        "title": "Assign PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": lines,
    })
    po.raise_for_status()
    return po.json()


async def _make_invoice(client, vendor_id, *, number, amount="1000.00", lines=None):
    r = await client.post(INV_URL, json={
        "vendor_id": vendor_id, "vendor_invoice_number": number,
        "amount": amount, "tax_amount": "0.00", "currency": "CAD",
        "invoice_date": "2026-07-09", "due_date": "2026-08-08",
        "line_items": lines or [{"description": "L1", "quantity": "1",
                                 "unit_price": amount, "line_total": amount}],
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _make_user(role="requester"):
    """直接建一个激活用户,返回其 id(指派对象)。"""
    import app.db.session as session_module
    from app.models.user import User
    uid = uuid.uuid4()
    async with session_module.AsyncSessionLocal() as db:
        db.add(User(id=uid, username=f"assignee-{uid.hex[:8]}",
                    email=f"{uid.hex[:8]}@test.local", full_name="Assignee Test",
                    role=role, is_active=True, hashed_password="x"))
        await db.commit()
    return uid


async def _open_match_task(invoice_id):
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        return (await db.execute(select(Task).where(
            Task.type == "match_invoice",
            Task.document_id == uuid.UUID(invoice_id),
            Task.is_completed.is_(False),
        ))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_assign_match_creates_task(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-01")
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-001")
    assignee = await _make_user()

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                json={"user_id": str(assignee)})
    assert r.status_code == 200, r.text

    task = await _open_match_task(inv["id"])
    assert task is not None
    assert task.assigned_user_id == assignee
    assert task.created_by is not None          # assigner 记录在 created_by
    assert task.document_number == inv["internal_ref"]


@pytest.mark.asyncio
async def test_assign_match_reassigns_existing_task(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-02")
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-002")
    first, second = await _make_user(), await _make_user()

    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(first)})
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(second)})
    assert r.status_code == 200

    task = await _open_match_task(inv["id"])
    assert task.assigned_user_id == second      # 改派,不是第二条任务
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        n = len((await db.execute(select(Task).where(
            Task.type == "match_invoice",
            Task.document_id == uuid.UUID(inv["id"]),
        ))).scalars().all())
    assert n == 1


@pytest.mark.asyncio
async def test_assign_match_requires_ap_role(requester_client, admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-03")
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-003")
    r = await requester_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                    json={"user_id": str(uuid.uuid4())})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_assign_match_rejects_matched_invoice(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-04")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-004")
    inv_line = inv["line_items"][0]["id"]
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv_line, "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "1000.00", "allocated_tax": "0.00"}]})
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match",
                                json={"user_id": str(await _make_user())})
    assert r.status_code == 409
```

注意:`requester_client` fixture 已存在于 conftest。若 `User` 模型必填字段与上不符(跑一次看报错),按模型实际列补齐 — 不改断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_invoice_assign.py -q`
Expected: 4 FAIL(assign-match 404 Not Found — 端点不存在)

- [ ] **Step 3: 实现**

`schemas/invoice.py` 追加:

```python
class AssignMatchRequest(BaseModel):
    user_id: uuid.UUID
```

`notification.py` 两处小改:

```python
# _infer_template 的映射 dict 追加两行:
        "match_invoice": "match_invoice_assigned",
        "review_match": "match_review_request",
# _dispatch 中 link 构造(~L160)改为复数修正:
    doc_path = "invoices" if task.document_type == "invoice" else task.document_type
    link = f"{system_url}/{doc_path}/{task.document_id}"
```

`invoices.py` 新端点(放在 match_invoice 端点之后):

```python
@router.post("/{invoice_id}/assign-match", response_model=InvoiceResponse)
async def assign_match(
    invoice_id: uuid.UUID,
    body: AssignMatchRequest,
    db: SessionDep,
    user: ApDep,
):
    """AP 将这张发票的 PO 匹配工作指派给某个用户(任何角色)。重复调用=改派。"""
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status not in ("unmatched", "exception"):
        raise HTTPException(status_code=409, detail=f"Invoice already in status '{inv.status}'")

    assignee = await db.get(User, body.user_id)
    if assignee is None or not assignee.is_active:
        raise HTTPException(status_code=404, detail="Assignee not found or inactive")

    assigner_id = uuid.UUID(user["sub"])
    existing = (await db.execute(select(Task).where(
        Task.type == "match_invoice",
        Task.document_type == "invoice",
        Task.document_id == inv.id,
        Task.is_completed.is_(False),
    ))).scalar_one_or_none()

    description = (
        f"You have been assigned to match invoice {inv.internal_ref} "
        f"({inv.vendor_name}, {inv.currency} {inv.total_amount}) to its purchase order(s). "
        f"Open the invoice and allocate its lines to the PO lines."
    )
    if existing is not None:
        existing.assigned_user_id = assignee.id
        existing.created_by = assigner_id
        existing.description = description
        task = existing
    else:
        task = Task(
            type="match_invoice", priority="normal",
            document_type="invoice", document_id=inv.id,
            document_number=inv.internal_ref,
            assigned_role="assigned",            # 非真实角色,防止角色池广播
            assigned_user_id=assignee.id,
            created_by=assigner_id,
            title=f"Match invoice {inv.internal_ref} to PO",
            description=description,
            vendor=inv.vendor_name, amount=inv.total_amount,
        )
        db.add(task)
    await db.flush()
    await db.refresh(task)
    fire_and_forget_notify(task, db, extra_vars={"invoice_number": inv.internal_ref})
    return inv
```

import 区补:`from app.schemas.invoice import AssignMatchRequest`(并入现有 import 块)。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_invoice_assign.py tests/test_invoice_allocations.py -q`
Expected: 全部 PASS(4 新 + 13 存量)

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/api/v1/invoices.py epms-api/app/schemas/invoice.py epms-api/app/services/notification.py epms-api/tests/test_invoice_assign.py
git commit -m "feat(epms-api): assign-match endpoint with task + notification"
```

---

### Task 5: 按任务授权 match + 发票可见性

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py`(match 端点守卫)
- Modify: `epms-api/app/crud/invoice.py`(`get_all` / `is_visible` 加任务授权)
- Test: `epms-api/tests/test_invoice_assign.py`(追加)

**Interfaces:**
- Consumes: Task 4 的 assign-match、`_open_task_doc_ids`(`app/core/access_scope.py` 已有)
- Produces: `_has_open_match_task(db, user_id, invoice_id) -> bool`(invoices.py 模块级 helper,Task 6 复用)
- Produces: `get_all(..., task_user_id: uuid.UUID | None = None)`、`is_visible` 认 `scope["user_id"]` 的 open-task 授权

- [ ] **Step 1: 写失败测试(追加到 test_invoice_assign.py)**

```python
async def _client_for_user(user_id, role="requester"):
    """给指定 user_id 造一个已登录 client(照抄 conftest._client + _make_token)。"""
    from tests.conftest import _client, _make_token
    return _client(_make_token(role, user_id=str(user_id)))


@pytest.mark.asyncio
async def test_assignee_can_match_and_see_invoice(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-05")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-005")
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})

    async with await _client_for_user(assignee) as c:
        # 可见性:详情不再 404
        assert (await c.get(f"{INV_URL}/{inv['id']}")).status_code == 200
        # 列表包含该发票
        listed = (await c.get(INV_URL)).json()
        assert any(i["id"] == inv["id"] for i in listed["items"])
        # 授权:零偏差 match 直接成功
        r = await c.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
            {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
             "po_line_id": po["line_items"][0]["id"],
             "allocated_amount": "1000.00", "allocated_tax": "0.00"}]})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "matched"

    # match 完成后任务闭环
    task = await _open_match_task(inv["id"])
    assert task is None


@pytest.mark.asyncio
async def test_user_without_task_still_403(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-06")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-006")
    stranger = await _make_user()
    async with await _client_for_user(stranger) as c:
        r = await c.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
            {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
             "po_line_id": po["line_items"][0]["id"],
             "allocated_amount": "1000.00", "allocated_tax": "0.00"}]})
        assert r.status_code == 403
```

注:`test_assignee_can_match_and_see_invoice` 同时验证 Task 6 的"零偏差直过 + 任务完成"—— 本 task 先让 403/404 消失,任务完成断言在 Task 6 实现后才会全绿;执行顺序上 Task 5、6 连续完成后一起跑通即可(两个 task 各自跑,允许本 task 结束时该测试仍剩"任务未完成"一处红,Task 6 收口)。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_invoice_assign.py -q -k "assignee_can_match or without_task"`
Expected: assignee 用例 404/403 失败;stranger 用例可能已过(现状就是 403)

- [ ] **Step 3: 实现**

`invoices.py`:

```python
async def _has_open_match_task(db, user_id: uuid.UUID, invoice_id: uuid.UUID) -> bool:
    row = (await db.execute(select(Task.id).where(
        Task.type == "match_invoice",
        Task.document_type == "invoice",
        Task.document_id == invoice_id,
        Task.assigned_user_id == user_id,
        Task.is_completed.is_(False),
    ))).scalar_one_or_none()
    return row is not None
```

match 端点守卫:签名 `user: ApDep` 改为 `user: CurrentUserPayload`,函数体开头:

```python
    caller_id = uuid.UUID(user["sub"])
    is_ap = user.get("role") in _AP_ROLES
    if not is_ap and not await _has_open_match_task(db, caller_id, invoice_id):
        raise HTTPException(status_code=403, detail="Not allowed to match this invoice")
```

列表与详情可见性:

```python
# list_invoices:get_all 调用追加参数
        task_user_id=uuid.UUID(user["sub"]),
# get_invoice 的 restrict 分支前追加:
    if scope["restrict"] and await _has_open_match_task(db, uuid.UUID(user["sub"]), invoice_id):
        return inv
```

`crud/invoice.py` `get_all`:加参数 `task_user_id: uuid.UUID | None = None`;在组装 or 条件的分支(见 L76-90 现有 `po_ids_subq`/`own_uploads` 逻辑)中并入:

```python
    if task_user_id is not None:
        from app.core.access_scope import _open_task_doc_ids
        conds.append(Invoice.id.in_(_open_task_doc_ids(task_user_id, "invoice")))
```

(具体嵌入位置:与现有 `own_uploads_user_id` 条件同一个 `or_` 列表;先读该函数当前实现再嵌,保持既有行为不变 — restrict 为空时依旧全量。)
`is_visible` 同样追加:

```python
    from app.core.access_scope import _open_task_doc_ids
    conds.append(Invoice.id.in_(_open_task_doc_ids(user_id, "invoice")))
```

注意:`view_invoice` 矩阵开关仍然拦在最前(列表返回空/详情 404)。任务授权用户通常是 requester,矩阵默认给 requester `view_invoice`=on(自己上传可见即证明);**不**为任务授权绕过矩阵开关 — 若某角色矩阵关了 view_invoice,先在 Admin 打开,这是既定的矩阵优先约定。

- [ ] **Step 4: 跑测试**

Run: `pytest tests/test_invoice_assign.py -q`
Expected: 除 `task is None` 断言(Task 6 收口)外全过;`test_user_without_task_still_403` PASS

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/api/v1/invoices.py epms-api/app/crud/invoice.py epms-api/tests/test_invoice_assign.py
git commit -m "feat(epms-api): task-scoped match authorization and invoice visibility"
```

---

### Task 6: 偏差复核流(后端核心)

**Files:**
- Modify: `epms-api/app/crud/invoice.py`(match() require_review 分支 + finalize/reject)
- Modify: `epms-api/app/api/v1/invoices.py`(match 端点接线 + match-review 端点)
- Modify: `epms-api/app/schemas/invoice.py`(MatchReviewRequest)
- Test: `epms-api/tests/test_invoice_review.py`(新文件)

**Interfaces:**
- Consumes: Task 4/5 的任务与授权;`within_tolerance`、`_match_tolerance_pct`(crud/invoice.py 已有)
- Produces: `invoice_crud.match(..., require_review: bool = False)`;`invoice_crud.review_match(db, invoice, action, note, reviewer_id) -> Invoice`
- Produces: `POST /invoices/{id}/match-review {action: "approve"|"reject", note?}`;新状态字符串 `"match_review"`

- [ ] **Step 1: 写失败测试**

```python
# epms-api/tests/test_invoice_review.py
"""被指派人 match 的偏差复核流。"""
import uuid

import pytest
from sqlalchemy import select

from tests.test_invoice_assign import (
    INV_URL, _client_for_user, _make_invoice, _make_po, _make_user, _make_vendor,
    _open_match_task,
)


async def _open_review_task(invoice_id):
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        return (await db.execute(select(Task).where(
            Task.type == "review_match",
            Task.document_id == uuid.UUID(invoice_id),
            Task.is_completed.is_(False),
        ))).scalar_one_or_none()


async def _assigned_variance_match(admin_client, *, code, number):
    """搭台:PO 1000,发票 900(有偏差),指派并由被指派人 match。返回 (inv, assignee)。"""
    v = await _make_vendor(admin_client, code)
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number=number, amount="900.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "900.00", "line_total": "900.00"}])
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})
    async with await _client_for_user(assignee) as c:
        r = await c.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
            {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
             "po_line_id": po["line_items"][0]["id"],
             "allocated_amount": "900.00", "allocated_tax": "0.00"}]})
        assert r.status_code == 200, r.text
        inv = r.json()
    return inv, assignee


@pytest.mark.asyncio
async def test_assignee_variance_goes_to_review(admin_client):
    inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-01", number="REV-001")
    assert inv["status"] == "match_review"
    assert await _open_match_task(inv["id"]) is None          # match 任务完成
    review = await _open_review_task(inv["id"])
    assert review is not None
    assert review.assigned_user_id is not None                 # 给 assigner


@pytest.mark.asyncio
async def test_review_approve_lands_by_tolerance(admin_client):
    inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-02", number="REV-002")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review", json={"action": "approve"})
    assert r.status_code == 200, r.text
    # 偏差 -100/1000 = -10%,默认容差 0 → exception(既有 tolerance 语义)
    assert r.json()["status"] == "exception"
    assert await _open_review_task(inv["id"]) is None


@pytest.mark.asyncio
async def test_review_reject_returns_unmatched_and_recreates_task(admin_client):
    inv, assignee = await _assigned_variance_match(admin_client, code="VND-REV-03", number="REV-003")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review",
                                json={"action": "reject", "note": "Wrong PO line"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "unmatched"
    task = await _open_match_task(inv["id"])
    assert task is not None and task.assigned_user_id == assignee
    assert "Wrong PO line" in (task.description or "")
    assert await _open_review_task(inv["id"]) is None


@pytest.mark.asyncio
async def test_review_reject_requires_note(admin_client):
    inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-04", number="REV-004")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review", json={"action": "reject"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_match_locked_while_in_review(admin_client):
    inv, _ = await _assigned_variance_match(admin_client, code="VND-REV-05", number="REV-005")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": str(uuid.uuid4())})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_ap_direct_match_unaffected(admin_client):
    """AP 自己 match 有偏差 → 照旧 exception,不进 review(现状回归)。"""
    v = await _make_vendor(admin_client, "VND-REV-06")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="REV-006", amount="900.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "900.00", "line_total": "900.00"}])
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "900.00", "allocated_tax": "0.00"}]})
    assert r.json()["status"] == "exception"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_invoice_review.py -q`
Expected: 全 FAIL(status 是 exception 而非 match_review;match-review 404)

- [ ] **Step 3: 实现**

`schemas/invoice.py`:

```python
from typing import Literal

class MatchReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None
```

`crud/invoice.py` — `match()` 加参数 `require_review: bool = False`;第 5 步 rollup 之后、现有 `if any_exception:` 状态落定处改为:

```python
    all_zero = all((row.variance or Decimal("0")) == Decimal("0") for row in new_rows)
    if require_review and not all_zero:
        invoice.status = "match_review"
        invoice.exception_reason = None
    elif any_exception:
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
```

新增 `review_match`(放 `rematch_from_existing` 之后):

```python
async def review_match(db: AsyncSession, invoice: Invoice, action: str,
                       note: str | None, reviewer_id: uuid.UUID) -> Invoice:
    """复核被指派人的 match:approve 按容差落定,reject 回 unmatched。"""
    if invoice.status != "match_review":
        raise ValueError(f"Invoice is not pending review (status '{invoice.status}')")
    if action == "approve":
        rows = (await db.execute(
            select(InvoicePoAllocation).where(InvoicePoAllocation.invoice_id == invoice.id)
        )).scalars().all()
        tolerance = await _match_tolerance_pct(db)
        any_exception = any(
            not within_tolerance(r.variance or Decimal("0"), r.variance_pct or Decimal("0"), tolerance)
            for r in rows
        )
        if any_exception:
            invoice.status = "exception"
            invoice.exception_reason = (
                "Reviewed: variance outside tolerance "
                f"(invoice total {invoice.total_amount} vs reference {invoice.po_total})"
            )
        else:
            invoice.status = "matched"
            invoice.exception_reason = (
                f"Reviewed and approved (variance: {invoice.variance:+.2f})"
                if invoice.variance else None
            )
    else:  # reject
        invoice.status = "unmatched"
        invoice.matched_at = None
        invoice.matched_by = None
        invoice.matched_by_name = None
        # 分摊行保留供参考;下次 match 会整体重建(match() 幂等删除)
    await db.flush()
    await db.refresh(invoice)
    return invoice
```

`invoices.py` match 端点接线(Task 5 守卫之后):

```python
    require_review = not is_ap
    ...
        result = await invoice_crud.match(db, inv, body, matched_by=caller_id,
                                          require_review=require_review)

        # 完成调用者的 match 任务(指派场景)
        my_task = (await db.execute(select(Task).where(
            Task.type == "match_invoice", Task.document_type == "invoice",
            Task.document_id == inv.id, Task.assigned_user_id == caller_id,
            Task.is_completed.is_(False),
        ))).scalar_one_or_none()
        if my_task is not None:
            from datetime import datetime, timezone
            my_task.is_completed = True
            my_task.completed_at = datetime.now(timezone.utc)
            my_task.completed_by = caller_id
            reviewer_id = my_task.created_by

        if result.status == "match_review" and my_task is not None:
            review = Task(
                type="review_match", priority="normal",
                document_type="invoice", document_id=inv.id,
                document_number=inv.internal_ref,
                assigned_role="ap_clerk",                    # created_by 为空时的角色池兜底
                assigned_user_id=reviewer_id,
                created_by=caller_id,
                title=f"Review match variance on invoice {inv.internal_ref}",
                description=(
                    f"Invoice {inv.internal_ref} was matched with a non-zero variance "
                    f"({result.variance}). Please review and approve or reject."
                ),
                vendor=inv.vendor_name, amount=inv.total_amount,
            )
            db.add(review)
            await db.flush()
            await db.refresh(review)
            fire_and_forget_notify(review, db, extra_vars={"invoice_number": inv.internal_ref})

        if result.status == "matched":
            await _notify_requester_create_pa(db, result)    # 原调用改为条件执行
        await finance_sync.sync_ap_invoice(db, result, token)
        return result
```

(把现有无条件 `_notify_requester_create_pa` 调用改成上面的条件形式。)

新端点:

```python
@router.post("/{invoice_id}/match-review", response_model=InvoiceResponse)
async def match_review(
    invoice_id: uuid.UUID,
    body: MatchReviewRequest,
    db: SessionDep,
    user: ApDep,
    token: BearerToken,
):
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if body.action == "reject" and not (body.note or "").strip():
        raise HTTPException(status_code=422, detail="A note is required when rejecting")
    reviewer_id = uuid.UUID(user["sub"])
    try:
        result = await invoice_crud.review_match(db, inv, body.action, body.note, reviewer_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 完成 open review 任务
    from datetime import datetime, timezone
    review_task = (await db.execute(select(Task).where(
        Task.type == "review_match", Task.document_type == "invoice",
        Task.document_id == inv.id, Task.is_completed.is_(False),
    ))).scalar_one_or_none()
    prev_assignee = review_task.created_by if review_task else None   # match 者
    if review_task is not None:
        review_task.is_completed = True
        review_task.completed_at = datetime.now(timezone.utc)
        review_task.completed_by = reviewer_id

    if body.action == "reject" and prev_assignee is not None:
        redo = Task(
            type="match_invoice", priority="normal",
            document_type="invoice", document_id=inv.id,
            document_number=inv.internal_ref,
            assigned_role="assigned", assigned_user_id=prev_assignee,
            created_by=reviewer_id,
            title=f"Re-match invoice {inv.internal_ref} to PO",
            description=(
                f"Your match of invoice {inv.internal_ref} was rejected: {body.note} "
                f"Please review the allocation and match again."
            ),
            vendor=inv.vendor_name, amount=inv.total_amount,
        )
        db.add(redo)
        await db.flush()
        await db.refresh(redo)
        fire_and_forget_notify(redo, db, extra_vars={"invoice_number": inv.internal_ref})

    if result.status == "matched":
        await _notify_requester_create_pa(db, result)
    await finance_sync.sync_ap_invoice(db, result, token)
    return result
```

- [ ] **Step 4: 跑测试**

Run: `pytest tests/test_invoice_review.py tests/test_invoice_assign.py tests/test_invoice_allocations.py -q`
Expected: 全 PASS(含 Task 5 遗留的 `task is None` 断言)

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/crud/invoice.py epms-api/app/api/v1/invoices.py epms-api/app/schemas/invoice.py epms-api/tests/test_invoice_review.py
git commit -m "feat(epms-api): variance review flow for assigned matches (match_review status)"
```

---

### Task 7: 响应带上当前被指派人

**Files:**
- Modify: `epms-api/app/schemas/invoice.py`(InvoiceResponse 两个可选字段)
- Modify: `epms-api/app/api/v1/invoices.py`(list/detail 批量注入)
- Test: `epms-api/tests/test_invoice_assign.py`(追加)

**Interfaces:**
- Produces: `InvoiceResponse.match_assignee_id: uuid.UUID | None`、`match_assignee_name: str | None`(前端显示 chip 与 canMatch 判定用)

- [ ] **Step 1: 失败测试(追加)**

```python
@pytest.mark.asyncio
async def test_response_surfaces_assignee(admin_client):
    v = await _make_vendor(admin_client, "VND-ASSIGN-07")
    inv = await _make_invoice(admin_client, v["id"], number="ASSIGN-007")
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})

    detail = (await admin_client.get(f"{INV_URL}/{inv['id']}")).json()
    assert detail["match_assignee_id"] == str(assignee)
    assert detail["match_assignee_name"] == "Assignee Test"

    listed = (await admin_client.get(INV_URL, params={"search": "ASSIGN-007"})).json()
    row = next(i for i in listed["items"] if i["id"] == inv["id"])
    assert row["match_assignee_id"] == str(assignee)
```

- [ ] **Step 2: 确认失败**(KeyError/None)

- [ ] **Step 3: 实现**

`schemas/invoice.py` `InvoiceResponse` 追加(带默认,老响应兼容):

```python
    match_assignee_id: uuid.UUID | None = None
    match_assignee_name: str | None = None
```

`invoices.py` helper + 注入:

```python
async def _attach_match_assignees(db, invoices: list) -> None:
    ids = [inv.id for inv in invoices]
    if not ids:
        return
    rows = (await db.execute(
        select(Task.document_id, Task.assigned_user_id, User.full_name)
        .join(User, User.id == Task.assigned_user_id, isouter=True)
        .where(Task.type == "match_invoice", Task.document_type == "invoice",
               Task.document_id.in_(ids), Task.is_completed.is_(False))
    )).all()
    by_doc = {r[0]: (r[1], r[2]) for r in rows}
    for inv in invoices:
        assignee = by_doc.get(inv.id)
        inv.match_assignee_id = assignee[0] if assignee else None
        inv.match_assignee_name = assignee[1] if assignee else None
```

`list_invoices` return 前:`await _attach_match_assignees(db, items)`;`get_invoice`、`assign_match` return 前:`await _attach_match_assignees(db, [inv])`。
(ORM 实例 setattr 即可被 `from_attributes` 读取;别忘了 match/review 端点的返回也走 `_attach_match_assignees(db, [result])`,保证前端刷新即时。)

- [ ] **Step 4: 跑测试**

Run: `pytest tests/test_invoice_assign.py tests/test_invoice_review.py -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/schemas/invoice.py epms-api/app/api/v1/invoices.py epms-api/tests/test_invoice_assign.py
git commit -m "feat(epms-api): surface open match assignment on invoice responses"
```

---

### Task 8: 前端(指派 UI + 复核面板 + 状态徽章)

**Files:**
- Modify: `epms/src/services/invoices.ts`(类型 + assignMatch/reviewMatch)
- Modify: `epms/src/hooks/useInvoices.ts`(useAssignMatch/useReviewMatch)
- Create: `epms/src/pages/invoices/AssignMatchDialog.tsx`
- Modify: `epms/src/pages/invoices/InvoiceListPage.tsx`(徽章、Upload 第二步入口、队列行入口、canMatch)
- Modify: `epms/src/pages/invoices/InvoiceDetailPage.tsx`(Assign 按钮 + Review 面板)

**Interfaces:**
- Consumes: Task 4-7 的端点与字段;`userService.listAll`(`@/services/users` 已有);`useAuthStore` 当前用户
- Produces: `invoiceService.assignMatch(id, userId)`、`invoiceService.reviewMatch(id, action, note?)`;`<AssignMatchDialog invoiceId currentAssigneeName onClose onAssigned />`

- [ ] **Step 1: services/invoices.ts**

```typescript
// InvoiceStatus 联合类型(在 invoices.ts 或其 types 定义处)加 'match_review'
// ApiInvoice 追加:
  match_assignee_id?: string | null
  match_assignee_name?: string | null
// invoiceService 追加:
  assignMatch: (id: string, userId: string) =>
    api.post<ApiInvoice>(`/invoices/${id}/assign-match`, { user_id: userId }),
  reviewMatch: (id: string, action: 'approve' | 'reject', note?: string) =>
    api.post<ApiInvoice>(`/invoices/${id}/match-review`, { action, note }),
```

- [ ] **Step 2: hooks/useInvoices.ts**(照抄 useMatchInvoice 模式,invalidate `['invoices']` 与 `['invoice', id]`)

```typescript
export function useAssignMatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, userId }: { id: string; userId: string }) =>
      invoiceService.assignMatch(id, userId),
    onSuccess: (_d, { id }) => {
      qc.invalidateQueries({ queryKey: ['invoices'] })
      qc.invalidateQueries({ queryKey: ['invoice', id] })
    },
  })
}

export function useReviewMatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, action, note }: { id: string; action: 'approve' | 'reject'; note?: string }) =>
      invoiceService.reviewMatch(id, action, note),
    onSuccess: (_d, { id }) => {
      qc.invalidateQueries({ queryKey: ['invoices'] })
      qc.invalidateQueries({ queryKey: ['invoice', id] })
    },
  })
}
```

- [ ] **Step 3: AssignMatchDialog.tsx**(新文件,modal 内嵌搜索选人;modal 用 createPortal 到 body)

```tsx
import { useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Search, UserPlus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAssignMatch } from '@/hooks/useInvoices'
import { userService } from '@/services/users'

export function AssignMatchDialog({ invoiceId, currentAssigneeName, onClose, onAssigned }: {
  invoiceId: string
  currentAssigneeName?: string | null
  onClose: () => void
  onAssigned?: () => void
}) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<{ id: string; name: string } | null>(null)
  const assign = useAssignMatch()

  const { data, isLoading } = useQuery({
    queryKey: ['users-all'],
    queryFn: () => userService.listAll(),     // listAll 翻页取全量(20 条截断陷阱)
    staleTime: 5 * 60_000,
  })
  const users = useMemo(() => {
    const items = data?.items ?? []
    const q = query.trim().toLowerCase()
    return q
      ? items.filter((u) => u.full_name.toLowerCase().includes(q) || u.username.toLowerCase().includes(q))
      : items
  }, [data, query])

  const submit = () => {
    if (!selected) return
    assign.mutate({ id: invoiceId, userId: selected.id }, {
      onSuccess: () => { onAssigned?.(); onClose() },
    })
  }

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-3.5">
          <div className="flex items-center gap-2">
            <UserPlus className="h-4 w-4 text-primary-600" />
            <h2 className="text-sm font-semibold text-neutral-900">
              {currentAssigneeName ? 'Reassign PO Matching' : 'Assign PO Matching'}
            </h2>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex flex-col gap-3 px-5 py-4 min-h-0">
          {currentAssigneeName && (
            <p className="text-xs text-neutral-500">
              Currently assigned to <span className="font-medium">{currentAssigneeName}</span> — picking a new
              person reassigns the task and notifies them.
            </p>
          )}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-neutral-400" />
            <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Search people by name..."
              className="w-full h-9 pl-8 pr-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto rounded-lg border border-neutral-200 divide-y divide-neutral-100">
            {isLoading ? (
              <div className="flex justify-center py-6 text-neutral-300"><Loader2 className="h-5 w-5 animate-spin" /></div>
            ) : users.length === 0 ? (
              <p className="py-6 text-center text-xs text-neutral-400">No users found</p>
            ) : users.map((u) => (
              <button key={u.id}
                onClick={() => setSelected({ id: u.id, name: u.full_name })}
                className={`w-full px-3 py-2 text-left text-sm hover:bg-primary-50 ${selected?.id === u.id ? 'bg-primary-50 font-medium' : ''}`}>
                {u.full_name}
                <span className="ml-2 text-xs text-neutral-400">{u.role}</span>
              </button>
            ))}
          </div>
          {assign.isError && (
            <p className="text-xs text-danger-600">
              {assign.error instanceof Error ? assign.error.message : 'Assign failed'}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-neutral-100 px-5 py-3.5">
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" disabled={!selected || assign.isPending} onClick={submit}>
            {assign.isPending ? 'Assigning…' : 'Assign & Notify'}
          </Button>
        </div>
      </div>
    </div>,
    document.body
  )
}
```

(users service 字段名以 `@/services/users` 实际类型为准 — 打开该文件核对 `full_name`/`username`/`role`,不符则用实际字段;不改逻辑。)

- [ ] **Step 4: InvoiceListPage.tsx**

1. `STATUS_CFG` 加:`match_review: { label: 'Pending Review', variant: 'info', dot: 'bg-primary-500' }`
2. Upload 第二步(Task 2026-07-08 加的 `createdInv` 屏):底部按钮区加
   `"Assign to someone instead"` — 打开 `AssignMatchDialog(invoiceId=createdInv.id)`,`onAssigned={() => onUploaded(createdInv.id)}`
3. Unmatched 队列行:在 Match 按钮旁加 Assign 按钮(仅 AP 角色显示,即 `MATCH_ROLES.has(role)`);行上显示 `inv.match_assignee_name` chip(有值时,样式随现有 badge)
4. `canMatch` 判定抽成:

```typescript
const canMatchInvoice = (inv: ApiInvoice) => {
  const me = useAuthStore.getState().user
  return (!!me?.role && MATCH_ROLES.has(me.role)) || (inv.match_assignee_id != null && inv.match_assignee_id === me?.id)
}
```

  队列行 Match 按钮与 MatchPanel 渲染改用它(被指派人打开列表时能对自己的发票展开分摊面板)。

- [ ] **Step 5: InvoiceDetailPage.tsx**

1. Header 动作区(unmatched/exception 且 AP):`Assign` / `Reassign(当前:{match_assignee_name})` 按钮 → AssignMatchDialog
2. `status === 'match_review'` 且 AP 角色:渲染 Review 面板(放在 PO Allocations 区块上方):

```tsx
{inv.status === 'match_review' && isAp && (
  <div className="sm:col-span-2 rounded-xl border border-primary-200 bg-primary-50 p-4 flex flex-col gap-3">
    <p className="text-sm font-semibold text-primary-800">Match pending review</p>
    <p className="text-xs text-neutral-600">
      This invoice was matched with a non-zero variance of {formatAmount(Number(inv.variance ?? 0), inv.currency)}
      {' '}(PO reference {formatAmount(Number(inv.po_total ?? 0), inv.currency)}). Approve to finalize the match,
      or reject to send it back to {inv.match_assignee_name ?? 'the assignee'}.
    </p>
    <textarea rows={2} value={reviewNote} onChange={(e) => setReviewNote(e.target.value)}
      placeholder="Review note (required to reject)..."
      className="px-3 py-2 rounded-lg border border-neutral-300 text-sm resize-none" />
    <div className="flex gap-2">
      <Button size="sm" disabled={reviewMutation.isPending}
        onClick={() => reviewMutation.mutate({ id: inv.id, action: 'approve', note: reviewNote || undefined })}>
        Approve Match
      </Button>
      <Button size="sm" variant="secondary" disabled={reviewMutation.isPending || !reviewNote.trim()}
        onClick={() => reviewMutation.mutate({ id: inv.id, action: 'reject', note: reviewNote })}>
        Reject — send back
      </Button>
    </div>
  </div>
)}
```

  (`reviewMutation = useReviewMatch()`;`isAp` 用页面已有的角色判断,没有则 `MATCH_ROLES` 从 ListPage 提到共享处或本地复制一份 Set;detail 页 STATUS 徽章配置若独立存在也要加 match_review。)

- [ ] **Step 6: Typecheck**

Run: `cd epms; node ../node_modules/typescript/bin/tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -E "Invoice(ListPage|DetailPage|AllocationPanel)|AssignMatchDialog|useInvoices|invoices\.ts"`
Expected: 无输出(改动文件零错误)

- [ ] **Step 7: Commit**

```bash
git add epms/src
git commit -m "feat(epms): assign-match UI, review panel, match_review badge"
```

---

### Task 9: 回归 + 本地走查 + 交付

- [ ] **Step 1: epms-api 全量回归**

Run: `pytest tests/ -q`(env 前缀同上)
Expected: 新增测试全过;失败集 == 基线 6 个(`POST /po/{id}/action` 404 家族),无新增

- [ ] **Step 2: 本地 dev 生效**(源码卷挂载,重启)

```bash
docker restart uniops_epms_api && sleep 12 && curl -sf http://localhost:8000/api/v1/health
# alembic z3 需在本地 dev 库应用:
docker exec uniops_epms_api alembic upgrade head
```

注意:worktree 代码不在容器卷里 — 本地走查前需把 worktree 改动同步回 `C:/Project/uniops`(或临时把 compose 卷指向 worktree)。最省事:走查放在合入 main、主工作区恢复正常后进行;至少完成 pytest + typecheck 两道门。

- [ ] **Step 3: 人工走查清单**(用户或执行者在浏览器)

1. 上传带税发票(如 36.99 + 4.81 HST)→ 详情页 Tax Lines 已有 HST-ON 一行
2. 上传发票 → Upload 第二步点 Assign → 选人 → 被指派人收到邮件(SMTP 配置了的话)+ Task Inbox 有任务
3. 被指派人登录 → 列表能看到该发票 → Match 零偏差 → 直接 matched
4. 再走一遍有偏差的 → 状态 Pending Review → AP 详情页 Approve/Reject 两条路各验一次
5. AP 自己 match 有偏差发票 → 照旧 exception

- [ ] **Step 4: 交付(需用户同意)**

```bash
# 在 worktree:squash 成两个 commit 合入 main
git checkout main && git pull
git merge --squash <feature-branch 到 Task 2 的 commit>   # 实操:git diff 取 Task1-2 文件单独提交
# 更简单的等价操作:从 feature 分支 cherry-pick/apply 两组文件集,分别 commit:
#   commit 1: epms-api tax_prefill + mdm_client + tests
#   commit 2: 其余全部(含 spec + plan 文档)
git push origin main
```

发布:TAG=<新 sha>,13 镜像全量 build+push,app server 命令清单给用户(**这次有迁移 z3**:清单要加 `./migrate-prod.sh` 或等价 `docker compose exec epms-api alembic upgrade head`)。

---

## Self-Review 记录

- **Spec 覆盖**:F1 规则(容差/唯一命中/fail-open/仅创建)→ Task 1-2;assign+改派+通知 → Task 4;任务授权+可见性 → Task 5;match_review 全流转 → Task 6;影响面表(徽章 T8/finance draft 兜底无需改/删除与 PATCH 门既有逻辑天然拦截/match 端点状态门 T6 测试)→ 覆盖;前端三入口+复核面板 → Task 8。PA 资格:matched 才触发 create_pa(T6 条件化)✓
- **占位符**:无 TBD;两处"以实际字段为准"(User 模型测试 helper、users service 字段)是对既有代码的核对指令,非留白
- **类型一致性**:`require_review`/`review_match`/`match_assignee_id`/`assignMatch` 等签名在 Interfaces 与代码块间已核对一致
