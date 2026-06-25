# Finance AP Invoice — EPMS 接入 Implementation Plan (Plan 2 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EPMS 在发票生命周期各关键点同步 upsert 发票头+税行到 finance 的 `ap_invoices`（一录入就 draft、matched→posted、删除→void），取代旧的单点 `/ap/post-invoice` 调用。

**Architecture:** 新增 `finance_client.upsert_ap_invoice`（HTTP，POST `/finance/v1/ap/invoices`，fail-open）+ `app/services/finance_sync.py`（从 EPMS invoice + 其 tax lines 构建 JSON payload、映射状态、调用 client）。在 invoices.py 的 create/match/update/resolve_exception/delete 端点调用它，替换现有 3 处 `finance_client.post_invoice`。

**Tech Stack:** FastAPI + SQLAlchemy async（epms-api）。测试：pytest + 本地 docker `uniops_postgres` `epms_test`（见 conftest，env 覆盖 `POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD=epms_dev POSTGRES_DB=epms`）。

**前置依赖：** Plan 1 已完成——finance `POST /finance/v1/ap/invoices`（UpsertIn：source/source_invoice_id/source_ref/vendor_*/amount/tax_amount/total_amount/currency/invoice_date/due_date/status/source_status/po_id/po_number/tax_lines[{line_no,tax_code,taxable_base,tax_amount,recoverable}]）已上线，status=posted 时自动触发 accrual。

**本轮 Git 策略：** 不提交（批次统一）。每个 Task 末「Checkpoint」只验证留工作区，**不要 git commit/push/branch/stash**。

**环境：** epms-api dev 容器 `--reload` 连生产库 10.10.50.20（当前无正式数据可当测试库）；改 epms-api 即热重载。单测走本地 `epms_test`。

---

## File Structure

- `epms-api/app/services/finance_client.py` — 修改：新增 `upsert_ap_invoice(*, payload, bearer_token)`。
- `epms-api/app/services/finance_sync.py` — 新建：`sync_ap_invoice(db, invoice, bearer_token, *, void=False)` + 状态映射。
- `epms-api/app/api/v1/invoices.py` — 修改：create/match/update/resolve_exception/delete 调 `finance_sync.sync_ap_invoice`；给 upload_invoice / delete_invoice 加 `token: BearerToken`；替换 3 处 `post_invoice`。
- `epms-api/tests/test_finance_sync.py` — 新建：状态映射 + 各生命周期触发同步的测试。

类型口径（贯穿）：
- `finance_client.upsert_ap_invoice(*, payload: dict, bearer_token: str) -> dict | None`（fail-open，错误返回 None）。
- `finance_sync.sync_ap_invoice(db, invoice: Invoice, bearer_token: str, *, void: bool = False) -> None`（fail-open）。
- 状态映射 `_ap_status(invoice_status: str, void: bool) -> str`：void→"void"；matched/approved→"posted"；paid→"paid"；其余(unmatched/exception)→"draft"。

---

## Task 1: finance_client.upsert_ap_invoice（HTTP，fail-open）

**Files:**
- Modify: `epms-api/app/services/finance_client.py`
- Test: `epms-api/tests/test_finance_sync.py`

- [ ] **Step 1: 写失败测试**

新建 `epms-api/tests/test_finance_sync.py`：

```python
"""EPMS → finance AP invoice sync tests."""
import uuid
import pytest


@pytest.mark.asyncio
async def test_upsert_ap_invoice_fail_open(monkeypatch):
    """Client returns None (not raise) when finance is unreachable."""
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
        payload={"source": "epms", "source_invoice_id": str(uuid.uuid4()),
                 "invoice_date": "2026-06-17", "status": "draft"},
        bearer_token="t",
    )
    assert out is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD=epms_dev POSTGRES_DB=epms python -m pytest tests/test_finance_sync.py::test_upsert_ap_invoice_fail_open -v`
Expected: FAIL（`AttributeError: upsert_ap_invoice`）

- [ ] **Step 3: 实现**

在 `epms-api/app/services/finance_client.py` 末尾追加（沿用文件已有的 `httpx`、`settings`、`logger`、`_TIMEOUT`）：

```python
async def upsert_ap_invoice(*, payload: dict, bearer_token: str) -> dict | None:
    """Upsert a normalized AP invoice header+tax into finance ap_invoices.

    Fail-open: finance unavailability must not block EPMS invoice ops (errors
    logged; a later re-sync / reconciliation re-posts). payload must be JSON-ready
    (uuids/dates/decimals already stringified by the caller).
    """
    url = f"{settings.FINANCE_API_URL}/finance/v1/ap/invoices"
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

- [ ] **Step 4: 运行确认通过**

Run: `cd epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD=epms_dev POSTGRES_DB=epms python -m pytest tests/test_finance_sync.py::test_upsert_ap_invoice_fail_open -v`
Expected: PASS

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 2: finance_sync.sync_ap_invoice（构建 payload + 状态映射）

**Files:**
- Create: `epms-api/app/services/finance_sync.py`
- Test: `epms-api/tests/test_finance_sync.py`

- [ ] **Step 1: 写失败测试**

APPEND 到 `tests/test_finance_sync.py`（顶部已 `import uuid, pytest`；下面用到的 helper 直接内联）：

```python
from datetime import date


async def _make_vendor(client, code):
    r = await client.post("/api/v1/vendors", json={
        "code": code, "name": "Sync Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD"})
    r.raise_for_status()
    return r.json()


def _inv_payload(vendor_id, **over):
    p = {"vendor_id": vendor_id, "vendor_invoice_number": "V-SYNC-1",
         "amount": "1000.00", "tax_amount": "130.00", "currency": "CAD",
         "invoice_date": "2026-06-17", "due_date": "2026-07-17",
         "line_items": [{"description": "x", "quantity": "1",
                         "unit_price": "1000.00", "line_total": "1000.00"}]}
    p.update(over)
    return p


@pytest.mark.asyncio
async def test_sync_builds_payload_and_maps_status(admin_client, monkeypatch):
    """sync_ap_invoice builds a JSON-ready payload, maps EPMS status → finance status."""
    from app.services import finance_sync
    from app.crud import invoice as invoice_crud

    captured = {}
    async def _fake_upsert(*, payload, bearer_token):
        captured["payload"] = payload
        captured["token"] = bearer_token
        return {"ok": True}
    monkeypatch.setattr(finance_sync.finance_client, "upsert_ap_invoice", _fake_upsert)

    v = await _make_vendor(admin_client, "VND-SYNC-01")
    inv = (await admin_client.post("/api/v1/invoices", json=_inv_payload(v["id"]))).json()

    # fetch the ORM invoice and sync as draft
    import app.db.session as sm
    async with sm.AsyncSessionLocal() as db:
        orm = await invoice_crud.get_by_id(db, uuid.UUID(inv["id"]))
        await finance_sync.sync_ap_invoice(db, orm, "tok-123")

    p = captured["payload"]
    assert captured["token"] == "tok-123"
    assert p["source"] == "epms"
    assert p["source_invoice_id"] == inv["id"]
    assert p["source_ref"] == inv["internal_ref"]
    assert p["status"] == "draft"          # unmatched → draft
    assert p["source_status"] == "unmatched"
    assert p["amount"] == "1000.00" and p["total_amount"] == "1130.00"
    assert p["invoice_date"] == "2026-06-17"

    # void path
    captured.clear()
    async with sm.AsyncSessionLocal() as db:
        orm = await invoice_crud.get_by_id(db, uuid.UUID(inv["id"]))
        await finance_sync.sync_ap_invoice(db, orm, "tok", void=True)
    assert captured["payload"]["status"] == "void"


def test_ap_status_mapping():
    from app.services.finance_sync import _ap_status
    assert _ap_status("unmatched", False) == "draft"
    assert _ap_status("exception", False) == "draft"
    assert _ap_status("matched", False) == "posted"
    assert _ap_status("approved", False) == "posted"
    assert _ap_status("paid", False) == "paid"
    assert _ap_status("matched", True) == "void"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD=epms_dev POSTGRES_DB=epms python -m pytest tests/test_finance_sync.py -k "maps_status or mapping" -v`
Expected: FAIL（`ModuleNotFoundError: app.services.finance_sync`）

- [ ] **Step 3: 实现**

新建 `epms-api/app/services/finance_sync.py`：

```python
"""Build + push a normalized AP invoice to finance ap_invoices (Plan 2).

EPMS owns the rich invoice (PO 3-way match, allocations, tax lines); finance
owns the normalized AP header. On every lifecycle change we upsert a JSON-ready
snapshot into finance (fail-open via finance_client)."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.invoice_tax_line import InvoiceTaxLine
from app.services import finance_client


def _ap_status(invoice_status: str, void: bool) -> str:
    if void:
        return "void"
    if invoice_status in ("matched", "approved"):
        return "posted"
    if invoice_status == "paid":
        return "paid"
    return "draft"   # unmatched / exception / anything else


async def sync_ap_invoice(db: AsyncSession, invoice: Invoice, bearer_token: str,
                          *, void: bool = False) -> None:
    """Upsert this EPMS invoice's normalized header+tax into finance. Fail-open."""
    tax_rows = (await db.execute(
        select(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == invoice.id)
        .order_by(InvoiceTaxLine.line_no)
    )).scalars().all()

    payload = {
        "source": "epms",
        "source_invoice_id": str(invoice.id),
        "source_ref": invoice.internal_ref,
        "vendor_id": str(invoice.vendor_id) if invoice.vendor_id else None,
        "vendor_name": invoice.vendor_name,
        "vendor_invoice_number": invoice.vendor_invoice_number,
        "amount": str(invoice.amount),
        "tax_amount": str(invoice.tax_amount),
        "total_amount": str(invoice.total_amount),
        "currency": invoice.currency,
        "invoice_date": invoice.invoice_date.isoformat(),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "status": _ap_status(invoice.status, void),
        "source_status": invoice.status,
        "po_id": str(invoice.po_id) if invoice.po_id else None,
        "po_number": invoice.po_number,
        "tax_lines": [
            {"line_no": t.line_no, "tax_code": t.tax_code,
             "taxable_base": str(t.taxable_amount) if t.taxable_amount is not None else "0",
             "tax_amount": str(t.tax_amount), "recoverable": t.recoverable}
            for t in tax_rows
        ],
    }
    await finance_client.upsert_ap_invoice(payload=payload, bearer_token=bearer_token)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD=epms_dev POSTGRES_DB=epms python -m pytest tests/test_finance_sync.py -v`
Expected: 全部 PASS（fail-open + payload/status 映射）。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 3: 在 invoices.py 端点接入 sync（替换 post_invoice）

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py`
- Test: `epms-api/tests/test_finance_sync.py`

- [ ] **Step 1: 写失败测试（端点触发同步）**

APPEND 到 `tests/test_finance_sync.py`：

```python
@pytest.mark.asyncio
async def test_upload_invoice_triggers_draft_sync(admin_client, monkeypatch):
    """Uploading an invoice upserts a draft AP invoice to finance."""
    import app.api.v1.invoices as invoices_api
    calls = []
    async def _spy(db, invoice, bearer_token, *, void=False):
        calls.append({"status": invoice.status, "void": void, "id": str(invoice.id)})
    monkeypatch.setattr(invoices_api.finance_sync, "sync_ap_invoice", _spy)

    v = await _make_vendor(admin_client, "VND-SYNC-UP-01")
    r = await admin_client.post("/api/v1/invoices", json=_inv_payload(v["id"]))
    assert r.status_code == 201
    assert len(calls) == 1
    assert calls[0]["status"] == "unmatched" and calls[0]["void"] is False


@pytest.mark.asyncio
async def test_delete_invoice_triggers_void_sync(admin_client, monkeypatch):
    import app.api.v1.invoices as invoices_api
    calls = []
    async def _spy(db, invoice, bearer_token, *, void=False):
        calls.append({"void": void})
    monkeypatch.setattr(invoices_api.finance_sync, "sync_ap_invoice", _spy)

    v = await _make_vendor(admin_client, "VND-SYNC-DEL-01")
    inv = (await admin_client.post("/api/v1/invoices", json=_inv_payload(v["id"]))).json()
    r = await admin_client.delete(f"/api/v1/invoices/{inv['id']}")
    assert r.status_code == 204
    assert any(c["void"] for c in calls)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD=epms_dev POSTGRES_DB=epms python -m pytest tests/test_finance_sync.py -k "triggers" -v`
Expected: FAIL（`invoices_api.finance_sync` 不存在 / 未调用）

- [ ] **Step 3: 实现 — import + 替换/新增调用**

在 `epms-api/app/api/v1/invoices.py`：

(a) 顶部 import 旁加：`from app.services import finance_sync`（保留现有 `from app.services import finance_client`，本计划不强制删 post_invoice）。

(b) `upload_invoice`（约 122-129 行）加 `token: BearerToken` 并在 create 后同步 draft：

```python
@router.post("", response_model=InvoiceResponse, status_code=201)
async def upload_invoice(body: InvoiceCreate, db: SessionDep, user: InvoiceUploadDep, token: BearerToken):
    vendor = await vendor_crud.get_by_id(db, body.vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    inv = await invoice_crud.create(
        db, body, vendor_name=vendor.name, uploaded_by=uuid.UUID(user["sub"])
    )
    await finance_sync.sync_ap_invoice(db, inv, token)   # draft, fail-open
    return inv
```

(c) `match_invoice`：把（约 205 行）
```python
        if result.status == "matched":
            await finance_client.post_invoice(invoice_id=result.id, bearer_token=token)
```
替换为：
```python
        # sync normalized AP header to finance (matched→posted triggers accrual there)
        await finance_sync.sync_ap_invoice(db, result, token)
```
（去掉 `if result.status == "matched"` 条件——exception 也要同步成 draft，保持 finance 全量。）

(d) `update_invoice`：把（约 177 行）
```python
        if result.status == "matched":
            await finance_client.post_invoice(invoice_id=result.id, bearer_token=token)
```
替换为：
```python
        await finance_sync.sync_ap_invoice(db, result, token)
```
注意：`update_invoice` 里 rematch 分支当前是 `if result.status == "matched": post_invoice`；改为无条件 `sync_ap_invoice(db, result, token)`，并把该调用移出 `if result.status == "matched"`，放在 rematch 块之后无条件执行（编辑后无论 matched/exception/unmatched 都同步当前状态）。若该端点存在「未匹配发票被编辑」路径（result 可能从未匹配），同样应同步 draft——确保 return 前总有一次 sync。

(e) `resolve_exception`：把（约 244 行）
```python
        if result.status == "matched":
            await finance_client.post_invoice(invoice_id=result.id, bearer_token=token)
```
替换为：
```python
        await finance_sync.sync_ap_invoice(db, result, token)
```

(f) `delete_invoice`（约 218-230 行）加 `token: BearerToken`，在删除前同步 void：

```python
@router.delete("/{invoice_id}", status_code=204)
async def delete_invoice(invoice_id: uuid.UUID, db: SessionDep, user: InvoiceUploadDep, token: BearerToken):
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        await finance_sync.sync_ap_invoice(db, inv, token, void=True)   # void in finance first
        await invoice_crud.delete(db, inv)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
```
> 注意：`delete` 的 ValueError 来自 `invoice_crud.delete`（状态不允许删）。sync 是 fail-open 不抛 ValueError，放在 try 内也安全；但若担心顺序，可先 `delete` 成功后再 sync void——本计划选「先 sync void 再 delete」，因 delete 成功后 ORM 对象已失效不便再读。若 delete 抛 409（未真正删除），finance 端已被标 void：可接受（下次该发票不存在；或 Plan 4 对账纠正）。实现时保持上面写法。

- [ ] **Step 4: 运行新测 + 回归**

Run: `cd epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD=epms_dev POSTGRES_DB=epms python -m pytest tests/test_finance_sync.py tests/test_invoice_allocations.py tests/test_invoices.py -v`
Expected：test_finance_sync 全过；allocation 套件仍全过；test_invoices 的既有 pre-existing 失败（/action 404、view_invoice 权限）数量不变（不因本改动新增）。`upload_invoice`/`delete_invoice` 加了 `token` 参数——确认未破坏既有调用（FastAPI 注入，测试客户端带 auth header）。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 4: 清理与验证（可选下线桥接）

**Files:**
- Modify: `epms-api/app/services/finance_client.py`（可选）

- [ ] **Step 1: 确认 post_invoice 不再被 EPMS 调用**

Run: `cd epms-api && grep -rn 'post_invoice' app/` 
Expected: 仅 `finance_client.py` 里的定义，`invoices.py` 已无调用。

- [ ] **Step 2: （可选）移除死代码**

若团队希望保持整洁：删除 `finance_client.py` 的 `post_invoice` 函数（EPMS 已不用；finance 侧 `/ap/post-invoice` 桥接保留给过渡，最终在 Plan 全部完成后由 finance 清理）。若不确定，保留不动（无害）。本计划默认**保留**，仅在此记录。

- [ ] **Step 3: 全量回归**

Run: `cd epms-api && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD=epms_dev POSTGRES_DB=epms python -m pytest tests/test_finance_sync.py tests/test_invoice_allocations.py -q`
Expected: 全绿。

- [ ] **Step 4: 容器健康（热重载到生产容器）**

Run: `docker logs uniops_epms_api --tail 10 | grep -iE 'startup complete|error|Traceback'; docker inspect uniops_epms_api --format '{{.State.Health.Status}}'`
Expected: `Application startup complete` + healthy（无 import 错误）。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Final Verification（本计划）

- [ ] `python -m pytest tests/test_finance_sync.py tests/test_invoice_allocations.py -q`（env 覆盖）→ 全绿。
- [ ] epms-api 容器 healthy、startup complete。
- [ ] 改动留工作区，不提交。

---

## 验收对照（spec → 本计划）

| spec 需求 | 本计划 Task |
|---|---|
| EPMS 同步 upsert AP 发票头（finance_client 模式）| Task 1, 2 |
| 一录入就 draft 全量 | Task 3(b) upload→draft |
| matched→posted（触发 finance accrual）| Task 3(c) match→sync |
| 编辑/异常解决重同步 | Task 3(d)(e) |
| 删除→void | Task 3(f) |
| fail-open | Task 1（client 不抛）|
| 税行映射(taxable_amount→taxable_base) | Task 2 payload |
| 取代旧 /ap/post-invoice | Task 3 替换 + Task 4 |
| 对账补偿(finance_synced 标志/脚本) | **非本计划**（spec 列为后续；Plan 4 迁移脚本可兼做对账）|
