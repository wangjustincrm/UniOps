# Finance AP Invoice — OA/expense-api 接入 Implementation Plan (Plan 3 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OA（expense-api）在发票生命周期同步 upsert 发票头到 finance 的 `ap_invoices`（创建/编辑→draft、被 PA 使用(used)→posted、作废→void），与 Plan 2 的 EPMS 接入对称。

**Architecture:** 复用 expense-api 已有的 `finance_client`（已有 `execute_payment` + `settings.finance_api_url`）新增 `upsert_ap_invoice`（fail-open）；新增 `app/services/finance_sync.py::sync_ap_invoice` 构建 OA→finance 的 JSON payload + 状态映射。在 `invoices.py` create/update 与 `pa.py` PA 创建处调用。

**Tech Stack:** FastAPI + SQLAlchemy async（expense-api）。测试沿用 expense-api 现有 conftest（先读 `tests/conftest.py` 用其 client fixture 与 DB 约定）。

**前置依赖：** Plan 1 完成（finance `POST /finance/v1/ap/invoices` upsert，UpsertIn 同 Plan 2）。Plan 2（EPMS 接入）完成——本计划是 OA 侧对称实现。

**本轮 Git 策略：** 不提交。每个 Task 末「Checkpoint」只验证留工作区，**不要 git commit/push/branch/stash**。

**环境：** expense-api dev 容器（`uniops_expense_api`）`--reload` 连生产库；改即热重载。`settings.finance_api_url` 默认 `http://localhost:8004`。

---

## File Structure

- `expense-api/app/services/finance_client.py` — 修改：新增 `upsert_ap_invoice(*, payload, bearer_token)`（fail-open，return None）。
- `expense-api/app/services/finance_sync.py` — 新建：`sync_ap_invoice(db, invoice, bearer_token, *, void=False)` + `_ap_status`。
- `expense-api/app/api/v1/invoices.py` — 修改：create/update 后同步 draft；加 `BearerTokenDep`。
- `expense-api/app/api/v1/pa.py` — 修改：PA 创建把发票置 used 后同步 posted；加 `BearerTokenDep`（若无）。
- `expense-api/tests/test_finance_ap_sync.py` — 新建：sync 触发测试。

类型口径（贯穿）：
- `finance_client.upsert_ap_invoice(*, payload: dict, bearer_token: str) -> dict | None`（fail-open）。
- `finance_sync.sync_ap_invoice(db, invoice: ExpenseInvoice, bearer_token: str, *, void: bool=False) -> None`。
- `_ap_status(invoice_status: str, void: bool)`：void→"void"；"used"→"posted"；其余(reviewed/uploaded)→"draft"。

OA→finance 字段映射：source="oa"，source_invoice_id=inv.id，source_ref=inv.invoice_number，vendor_id/vendor_name（可空），vendor_invoice_number=inv.invoice_number，amount=inv.subtotal（税前），tax_amount，total_amount，currency，invoice_date=**inv.invoice_date 或回退 inv.created_at.date()**（finance 要求非空），due_date（可空），status（映射），source_status=inv.status，po_id=None，po_number=None，tax_lines=[]（OA 仅 header 税，无 tax_code）。

---

## Task 1: expense-api finance_client.upsert_ap_invoice

**Files:**
- Modify: `expense-api/app/services/finance_client.py`
- Test: `expense-api/tests/test_finance_ap_sync.py`

- [ ] **Step 1: 读 expense-api 测试约定**

先读 `expense-api/tests/conftest.py`，记下：HTTP client fixture 名（如 `client` / `admin_client`）、DB session 获取方式、跑 pytest 的 DB 环境（很可能也是本地 docker `uniops_postgres` 的某测试库）。下面测试里把 `<<CLIENT_FIXTURE>>` 换成实际 fixture 名。

- [ ] **Step 2: 写失败测试**

新建 `expense-api/tests/test_finance_ap_sync.py`：

```python
"""OA → finance AP invoice sync tests."""
import uuid
import pytest


@pytest.mark.asyncio
async def test_upsert_ap_invoice_fail_open(monkeypatch):
    from app.services import finance_client

    class _BoomClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            import httpx
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(finance_client.httpx, "AsyncClient", _BoomClient)
    out = await finance_client.upsert_ap_invoice(
        payload={"source": "oa", "source_invoice_id": str(uuid.uuid4()),
                 "invoice_date": "2026-06-17", "status": "draft"},
        bearer_token="t",
    )
    assert out is None
```

- [ ] **Step 3: 运行确认失败**

Run（用 expense-api 的测试 DB env，参照 conftest；若与 epms 相同则）：`cd expense-api && python -m pytest tests/test_finance_ap_sync.py::test_upsert_ap_invoice_fail_open -v`
Expected: FAIL（`AttributeError: upsert_ap_invoice`）

- [ ] **Step 4: 实现**

在 `expense-api/app/services/finance_client.py` 末尾追加（复用已有 `httpx`、`settings`、`logger`、`_TIMEOUT`；注意此服务用 `settings.finance_api_url` 小写）：

```python
async def upsert_ap_invoice(*, payload: dict, bearer_token: str) -> dict | None:
    """Upsert a normalized AP invoice header into finance ap_invoices (source='oa').

    Fail-open: finance unavailability must not block OA invoice ops (logged; a
    later re-sync / reconciliation re-posts). payload must be JSON-ready."""
    url = f"{settings.finance_api_url}/finance/v1/ap/invoices"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        logger.error("finance-api /ap/invoices upsert failed for %s/%s: %s — failing open",
                     payload.get("source"), payload.get("source_invoice_id"), e)
        return None
```

- [ ] **Step 5: 运行确认通过**

Run: `cd expense-api && python -m pytest tests/test_finance_ap_sync.py::test_upsert_ap_invoice_fail_open -v`
Expected: PASS

- [ ] **Step 6: Checkpoint（不提交）**

---

## Task 2: expense-api finance_sync.sync_ap_invoice

**Files:**
- Create: `expense-api/app/services/finance_sync.py`
- Test: `expense-api/tests/test_finance_ap_sync.py`

- [ ] **Step 1: 写失败测试（状态映射，纯单元）**

APPEND 到 `tests/test_finance_ap_sync.py`：

```python
def test_ap_status_mapping():
    from app.services.finance_sync import _ap_status
    assert _ap_status("reviewed", False) == "draft"
    assert _ap_status("uploaded", False) == "draft"
    assert _ap_status("used", False) == "posted"
    assert _ap_status("used", True) == "void"


@pytest.mark.asyncio
async def test_sync_builds_oa_payload(monkeypatch):
    """sync_ap_invoice builds a JSON-ready OA payload; invoice_date falls back to created_at."""
    import datetime as _dt
    from app.services import finance_sync

    captured = {}
    async def _fake_upsert(*, payload, bearer_token):
        captured["payload"] = payload
        return {"ok": True}
    monkeypatch.setattr(finance_sync.finance_client, "upsert_ap_invoice", _fake_upsert)

    class _Inv:  # lightweight stand-in matching ExpenseInvoice fields used
        id = uuid.uuid4()
        invoice_number = "INV-OA-1"
        vendor_id = uuid.uuid4()
        vendor_name = "ULINE"
        invoice_date = None                       # OA may lack it
        due_date = None
        currency = "CAD"
        subtotal = "1000.00"
        tax_amount = "130.00"
        total_amount = "1130.00"
        status = "used"
        created_at = _dt.datetime(2026, 6, 20, 12, 0, tzinfo=_dt.timezone.utc)

    await finance_sync.sync_ap_invoice(db=None, invoice=_Inv(), bearer_token="t")
    p = captured["payload"]
    assert p["source"] == "oa"
    assert p["source_invoice_id"] == str(_Inv.id)
    assert p["status"] == "posted"                # used → posted
    assert p["amount"] == "1000.00" and p["total_amount"] == "1130.00"
    assert p["invoice_date"] == "2026-06-20"      # fallback to created_at date
    assert p["due_date"] is None
    assert p["tax_lines"] == []
```

> 注：OA sync 不需要查税行（OA 无 tax_code 税行），所以 `sync_ap_invoice` 不读 db 即可构 payload——测试传 `db=None` 验证这一点。若你的实现需要 db，请改测试为真实 invoice + db_session（与 conftest 一致），但优先保持 OA sync 不依赖 db 查询。

- [ ] **Step 2: 运行确认失败**

Run: `cd expense-api && python -m pytest tests/test_finance_ap_sync.py -k "mapping or oa_payload" -v`
Expected: FAIL（`ModuleNotFoundError: app.services.finance_sync`）

- [ ] **Step 3: 实现**

新建 `expense-api/app/services/finance_sync.py`：

```python
"""Build + push a normalized AP invoice to finance ap_invoices (Plan 3, OA side).

OA (expense-api) owns the OCR'd vendor invoice; finance owns the normalized AP
header. On lifecycle changes we upsert a JSON-ready snapshot into finance
(fail-open via finance_client). OA has only header tax (no tax_code lines)."""
from app.services import finance_client


def _ap_status(invoice_status: str, void: bool) -> str:
    if void:
        return "void"
    if invoice_status == "used":
        return "posted"
    return "draft"   # reviewed / uploaded / anything else


async def sync_ap_invoice(db, invoice, bearer_token: str, *, void: bool = False) -> None:
    """Upsert this OA expense_invoice's normalized header into finance. Fail-open.
    `db` is accepted for signature symmetry with the EPMS sync but unused (OA has
    no tax_code lines to load)."""
    inv_date = invoice.invoice_date or invoice.created_at.date()
    payload = {
        "source": "oa",
        "source_invoice_id": str(invoice.id),
        "source_ref": invoice.invoice_number,
        "vendor_id": str(invoice.vendor_id) if invoice.vendor_id else None,
        "vendor_name": invoice.vendor_name,
        "vendor_invoice_number": invoice.invoice_number,
        "amount": str(invoice.subtotal),
        "tax_amount": str(invoice.tax_amount),
        "total_amount": str(invoice.total_amount),
        "currency": invoice.currency,
        "invoice_date": inv_date.isoformat(),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "status": _ap_status(invoice.status, void),
        "source_status": invoice.status,
        "po_id": None,
        "po_number": None,
        "tax_lines": [],
    }
    await finance_client.upsert_ap_invoice(payload=payload, bearer_token=bearer_token)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd expense-api && python -m pytest tests/test_finance_ap_sync.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 3: 在 invoices.py + pa.py 接入 sync

**Files:**
- Modify: `expense-api/app/api/v1/invoices.py`（create_invoice、update_invoice）
- Modify: `expense-api/app/api/v1/pa.py`（PA 创建处，发票置 used 后）
- Test: `expense-api/tests/test_finance_ap_sync.py`

- [ ] **Step 1: 写失败测试（端点触发）**

> 用 expense-api conftest 的 client fixture（Step 1 已查；下面写 `<<CLIENT_FIXTURE>>` 替换为实名，如 `client`）。create_invoice 需要 OCR 字段 body——参照 `tests/test_invoices.py`（已存在）里 create 的请求体构造，复用其 helper/payload。

APPEND（把 `<<CLIENT_FIXTURE>>` 换成实名，invoice 创建 body 参照现有 test_invoices.py）：

```python
@pytest.mark.asyncio
async def test_create_invoice_triggers_draft_sync(<<CLIENT_FIXTURE>>, monkeypatch):
    import app.api.v1.invoices as invoices_api
    calls = []
    async def _spy(db, invoice, bearer_token, *, void=False):
        calls.append({"status": invoice.status, "void": void})
    monkeypatch.setattr(invoices_api.finance_sync, "sync_ap_invoice", _spy)

    # build the create body the same way tests/test_invoices.py does:
    body = { ... }   # ← copy from existing test_invoices.py create payload
    r = await <<CLIENT_FIXTURE>>.post("/api/v1/invoices", json=body)
    assert r.status_code == 201
    assert len(calls) == 1 and calls[0]["status"] == "reviewed" and calls[0]["void"] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd expense-api && python -m pytest tests/test_finance_ap_sync.py -k "draft_sync" -v`
Expected: FAIL（未接入）

- [ ] **Step 3: 实现 — invoices.py**

(a) 顶部加 `from app.services import finance_sync` 和（若未导入）`from app.core.deps import BearerTokenDep`。

(b) `create_invoice`：签名加 `token: BearerTokenDep`；在 `return InvoiceResponse.model_validate(inv)` 之前加：
```python
    await finance_sync.sync_ap_invoice(db, inv, token)   # draft, fail-open
```

(c) `update_invoice`：签名加 `token: BearerTokenDep`；在更新提交/return 前加同样一行 `await finance_sync.sync_ap_invoice(db, inv, token)`（编辑后状态仍 reviewed → draft 重同步）。

### Step 4 — 实现 pa.py（used→posted）

在 `expense-api/app/api/v1/pa.py` 的 PA 创建端点（约 89-138 行，设置 `inv.status="used"; inv.pa_id=...; inv.pa_number=...` 处）：
- 端点签名加 `token: BearerTokenDep`（若已有则复用）。顶部 import `from app.services import finance_sync`。
- 在把 `inv` 置 used、`db.flush()`/commit 之后、return 之前加：
```python
    await finance_sync.sync_ap_invoice(db, inv, token)   # used → posted (triggers finance accrual)
```
确保此时 `inv.status == "used"`（已在前面赋值），使映射得 posted。

### Step 5 — 运行新测 + 回归

Run: `cd expense-api && python -m pytest tests/test_finance_ap_sync.py tests/test_invoices.py -v`
Expected：finance_ap_sync 全过；test_invoices 既有用例不因新增 `token` 参数失败（测试客户端带 auth header，BearerTokenDep 可解析）。若 pa 相关测试存在，确认 used 流程仍通过。

- [ ] **Step 6: Checkpoint（不提交）**

---

## Task 4: 验证 + 容器健康

- [ ] **Step 1: 全量相关回归**

Run: `cd expense-api && python -m pytest tests/test_finance_ap_sync.py tests/test_invoices.py tests/test_pa.py -q`（若 test_pa.py 存在）
Expected: 全绿（既有 pre-existing 失败不新增）。

- [ ] **Step 2: 容器健康（热重载到生产容器）**

Run: `docker logs uniops_expense_api --tail 12 | grep -iE 'startup complete|error|Traceback|Reloading'; docker inspect uniops_expense_api --format '{{.State.Health.Status}}'`
Expected: `Application startup complete` + healthy（无 import 错误）。

- [ ] **Step 3: 冒烟 — OA 发票进入 finance AP（可选，需 finance 容器在）**

> 若想端到端确认：用 admin token 创建一张 OA 发票，再查 finance `GET /finance/v1/ap/invoices?source=oa` 应能看到该发票（draft）。finance 容器连同一生产库。非必须，CI 测试已覆盖逻辑。

- [ ] **Step 4: Checkpoint（不提交）**

---

## Final Verification（本计划）

- [ ] `cd expense-api && python -m pytest tests/test_finance_ap_sync.py tests/test_invoices.py -q` → 全绿。
- [ ] expense-api 容器 healthy、startup complete。
- [ ] 改动留工作区，不提交。

---

## 非目标 / 遗留

- **付款 → ap_invoices 回写**（跨切面 seam）：finance 付款执行器（payment_execute）目前更新 EPMS 镜像 Invoice / PA，不更新 `ap_invoices.paid_amount/status`。OA 的 PA-DIR 付款同理。所以 paid/partially_paid 暂不反映到新 AP 视图（open-items/aging）。**本计划不处理**——建议单列一个小任务/Plan（finance payment_execute 按 source+source_invoice_id 回写 ap_invoices），在 Plan 4 或之后做。
- OA 发票**作废/删除 → void**：OA 无用户级删除，仅 admin registry 删除（`app/admin/registry.py` 的 `_invoice_delete`）。本计划不接 void（低频；Plan 4 对账可纠正）。如需，可在 admin 删除钩子加一次 `sync_ap_invoice(..., void=True)`。

## 验收对照（spec → 本计划）

| spec 需求 | 本计划 Task |
|---|---|
| OA 同步 upsert AP 发票头（finance_client 模式）| Task 1, 2 |
| OA 创建/编辑 → draft 进 finance | Task 3 (create/update) |
| OA used(PA 建立) → posted（触发 accrual）| Task 3 (pa.py) |
| invoice_date 可空 → 回退 created_at | Task 2 payload |
| fail-open | Task 1 |
| OA 无税行 → header tax(uncoded) | Task 2 (tax_lines=[]) |
| 付款回写 ap_invoices | **非本计划**（见非目标）|
| OA void | **非本计划**（见非目标）|
