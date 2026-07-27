# Invoice 非PO费用行标记 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Invoice 的某些行(shipping/packaging 等杂费)可被右键标记为「非PO费用」,不参与 PO 匹配但其金额计入平账,从而放开被这类行卡住的 Match PO。

**Architecture:** 复用 `invoice.line_items`(JSONB)存标记(`non_po_fee`/`non_po_note`),零迁移。前端在 Allocation 面板右键标记、灰显、移出拖拽池,并把标记合计计入平账;match 请求新增 `non_po_lines` 数组,后端 `match()` 把标记写回 line_items 并在完整性校验里加上被标记行合计。AP 发票本就按发票头全额同步(含杂费),finance 侧不改。

**Tech Stack:** epms-api(FastAPI + SQLAlchemy async + Pydantic v2 + pytest);epms 前端(React + TS 5.9.3 + Vite + TanStack Query;无单测框架,验证用 `tsc` + `eslint`)。

## Global Constraints

- UI 面向用户字符串**全英文**;注释可中文。
- 前端 typecheck 用 `npx tsc -p tsconfig.app.json --noEmit`(TS 5.9.3,**不要**带 `--ignoreDeprecations 6.0`);epms 前端 tsc **基线 59 个存量错误**,改动**不得增加**错误数。lint 用 `npx eslint <改动文件>`(整仓 220 存量,只看单文件不新增)。
- 后端测试库是 **epms_test**(不是 dev 的 epms),须覆盖 `POSTGRES_*` 到本地 docker `uniops_postgres`;**同一时刻只跑一个 epms 套件**(共享测试库,并发会互相 drop_all)。验证要**正面证据**(断言具体值/状态),不靠"无输出=通过"。
- 金额一律 **税前**口径:`invoice.amount`、`line_total`、`allocated_amount` 都是税前;税在发票头 reconcile,非PO行不单独处理税。
- 浮层(右键菜单)用 `createPortal` 到 body + `position: fixed` + 点外关闭,避开祖先 `overflow` 裁剪。
- 零数据库迁移。不动 `finance_sync` / PA / `payment_execute`(见 spec「范围外」)。
- 每个逻辑单元正式 commit,不积压 WIP。分支 `feature/invoice-non-po-fee-lines`,worktree `c:/Project/uniops-nonpo-fee`。

---

### Task 1: 后端 — `non_po_lines` 支持(schema + `match()` + 测试)

**Files:**
- Modify: `epms-api/app/schemas/invoice.py`(`InvoiceLineItem` 加两字段;新增 `NonPoLineInput`;`InvoiceMatchRequest` 加 `non_po_lines`)
- Modify: `epms-api/app/crud/invoice.py:279-286`(完整性校验前插入非PO行处理)
- Test: `epms-api/tests/test_invoice_allocations.py`(追加 3 个测试)

**Interfaces:**
- Produces(前后端契约):match 请求体新增可选字段
  `non_po_lines: [{ line_id: UUID, note: str | null }]`。
- Produces:`InvoiceResponse.line_items[]` 现在会带 `non_po_fee: bool`、`non_po_note: str | null`。
- Consumes:现有 `InvoiceMatchRequest.allocations`、`invoice.line_items`(JSONB list[dict],每项有 `id`、`line_total`)。

- [ ] **Step 1: 写失败测试(平账 + 持久化 + 备注)**

在 `epms-api/tests/test_invoice_allocations.py` 末尾追加:

```python
@pytest.mark.asyncio
async def test_non_po_fee_line_lets_mixed_invoice_match(admin_client):
    """含 shipping 行的发票:goods 分到 PO,shipping 标记为非PO费用 →
    goods_alloc + 非PO合计 = 发票税前额 → matched;标记与备注持久化。"""
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-NONPO-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "Widget", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    po_line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], vendor_invoice_number="INV-NONPO-01",
        amount="1150.00", tax_amount="0.00",
        line_items=[
            {"description": "Widget", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"},
            {"description": "Shipping & handling", "quantity": "1", "unit_price": "150.00", "line_total": "150.00"},
        ]))).json()
    goods_line = inv["line_items"][0]["id"]
    ship_line = inv["line_items"][1]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [
            {"invoice_line_id": goods_line, "po_id": po["id"], "po_line_id": po_line,
             "allocated_amount": "1000.00", "allocated_tax": "0.00"},
        ],
        "non_po_lines": [{"line_id": ship_line, "note": "freight"}],
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert len(data["allocations"]) == 1          # shipping 不产生 allocation
    ship = next(li for li in data["line_items"] if li["id"] == ship_line)
    goods = next(li for li in data["line_items"] if li["id"] == goods_line)
    assert ship["non_po_fee"] is True
    assert ship["non_po_note"] == "freight"
    assert goods["non_po_fee"] is False           # 未标记的行默认 False
```

- [ ] **Step 2: 跑测试确认失败**

Run(在 `epms-api/` 下,套用本仓 epms 测试库 env,串行):
```bash
python -m pytest tests/test_invoice_allocations.py::test_non_po_fee_line_lets_mixed_invoice_match -v
```
Expected: FAIL —— 当前 422(`AllocationImbalance`:1000 ≠ 1150),或 `non_po_lines` 被忽略、`line_items` 无 `non_po_fee` 字段。

- [ ] **Step 3: 改 schema — `epms-api/app/schemas/invoice.py`**

`InvoiceLineItem` 加两字段(放在 `line_total` 之后):
```python
class InvoiceLineItem(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), ge=0)
    unit: str | None = Field(default=None, max_length=50)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    line_total: Decimal = Field(default=Decimal("0"), ge=0)
    # 非PO费用标记(shipping/packaging 等):不参与 PO 匹配,金额照付(随发票头)
    non_po_fee: bool = False
    non_po_note: str | None = Field(default=None, max_length=500)
```

在 `AllocationInput` 之后、`InvoiceMatchRequest` 之前新增:
```python
class NonPoLineInput(BaseModel):
    line_id: uuid.UUID
    note: str | None = Field(default=None, max_length=500)
```

`InvoiceMatchRequest` 加字段:
```python
class InvoiceMatchRequest(BaseModel):
    # New multi-PO path: when provided, takes priority.
    allocations: list[AllocationInput] | None = None
    # 非PO费用行:排除出匹配,但其 line_total 计入平账(照付)
    non_po_lines: list[NonPoLineInput] | None = None
    # Legacy single-PO path (existing tests / PATCH re-match / pre-rework UI).
    po_id: uuid.UUID | None = None
    gr_id: uuid.UUID | None = None
    gr_ids: list[uuid.UUID] | None = None
    po_line_ids: list[uuid.UUID] | None = None
```

- [ ] **Step 4: 改 `match()` — `epms-api/app/crud/invoice.py`**

先确认文件顶部已 import `flag_modified`;若无,加:
```python
from sqlalchemy.orm.attributes import flag_modified
```

在 `match()` 里、**现完整性校验 `alloc_total`(约 282 行)之前**插入非PO行处理,并把校验改为加上被标记行合计。即把:
```python
    # 1. integrity: allocations carry PRE-TAX amounts ...
    alloc_total = sum((a.allocated_amount for a in allocs), Decimal("0"))
    if abs(alloc_total - invoice.amount) > Decimal("0.01"):
        raise AllocationImbalance(
            f"Allocations total {alloc_total} must equal invoice pre-tax amount {invoice.amount}"
        )
```
替换为:
```python
    # 0. 非PO费用行:排除出 PO 匹配,但其税前 line_total 计入平账(照付,随发票头
    # 走 AP)。每次 match 以入参为准重写标记 —— 未列出的行清除标记(支持 unmark)。
    non_po_map: dict[str, str | None] = {
        str(n.line_id): n.note for n in (req.non_po_lines or [])
    }
    excluded_total = Decimal("0")
    if invoice.line_items:
        for li in invoice.line_items:
            lid = str(li.get("id"))
            if lid in non_po_map:
                li["non_po_fee"] = True
                li["non_po_note"] = non_po_map[lid]
                excluded_total += Decimal(str(li.get("line_total") or "0"))
            else:
                li["non_po_fee"] = False
                li["non_po_note"] = None
        flag_modified(invoice, "line_items")

    # 1. integrity: allocations carry PRE-TAX amounts (invoice/PO lines are
    # pre-tax; tax reconciles at the invoice header), so pre-tax allocations
    # PLUS non-PO fee lines must equal the invoice's pre-tax amount.
    alloc_total = sum((a.allocated_amount for a in allocs), Decimal("0"))
    if abs(alloc_total + excluded_total - invoice.amount) > Decimal("0.01"):
        raise AllocationImbalance(
            f"Allocations {alloc_total} + non-PO fees {excluded_total} must equal "
            f"invoice pre-tax amount {invoice.amount}"
        )
```

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
python -m pytest tests/test_invoice_allocations.py::test_non_po_fee_line_lets_mixed_invoice_match -v
```
Expected: PASS。

- [ ] **Step 6: 写 422 门禁回归测试 + unmark 测试**

追加:
```python
@pytest.mark.asyncio
async def test_unmarked_fee_line_still_imbalances_422(admin_client):
    """同样的混合发票,若不标记 shipping 也不分配它 → 仍 422(证明门禁未被架空)。"""
    v = await _make_vendor(admin_client, "VND-NONPO-422")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "Widget", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    po_line = po["line_items"][0]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], vendor_invoice_number="INV-NONPO-422",
        amount="1150.00", tax_amount="0.00",
        line_items=[
            {"description": "Widget", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"},
            {"description": "Shipping", "quantity": "1", "unit_price": "150.00", "line_total": "150.00"},
        ]))).json()
    goods_line = inv["line_items"][0]["id"]
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": goods_line, "po_id": po["id"], "po_line_id": po_line,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
    ]})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_non_po_fee_unmark_on_rematch_clears_flag(admin_client):
    """re-match 以入参为准:把原先标记的 shipping 改成分配到真实 PO 行且不再传
    non_po_lines → 标记被清除。用 exception 态(可 re-match)构造。"""
    await _ensure_company_config()
    v = await _make_vendor(admin_client, "VND-NONPO-UNMARK")
    # goods PO 行 500(小于开票 1000 → 超开触发 exception,便于随后 re-match);
    # shipping PO 行 150(第二次匹配用)
    po = await _make_issued_po(admin_client, v["id"], lines=[
        {"description": "Widget", "qty": "1", "unit": "EA", "unit_price": "500.00"},
        {"description": "Freight line", "qty": "1", "unit": "EA", "unit_price": "150.00"},
    ])
    po_goods = po["line_items"][0]["id"]
    po_ship = po["line_items"][1]["id"]
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], vendor_invoice_number="INV-NONPO-UNMARK",
        amount="1150.00", tax_amount="0.00",
        line_items=[
            {"description": "Widget", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"},
            {"description": "Shipping", "quantity": "1", "unit_price": "150.00", "line_total": "150.00"},
        ]))).json()
    goods_line = inv["line_items"][0]["id"]
    ship_line = inv["line_items"][1]["id"]

    # match #1: goods 分到 500(超开 → exception),shipping 标记非PO
    r1 = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [{"invoice_line_id": goods_line, "po_id": po["id"],
                         "po_line_id": po_goods, "allocated_amount": "1000.00", "allocated_tax": "0.00"}],
        "non_po_lines": [{"line_id": ship_line, "note": "freight"}],
    })
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "exception"
    ship1 = next(li for li in r1.json()["line_items"] if li["id"] == ship_line)
    assert ship1["non_po_fee"] is True

    # match #2(exception 可 re-match):shipping 改分到真实 PO 行,不传 non_po_lines
    r2 = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": goods_line, "po_id": po["id"], "po_line_id": po_goods,
         "allocated_amount": "1000.00", "allocated_tax": "0.00"},
        {"invoice_line_id": ship_line, "po_id": po["id"], "po_line_id": po_ship,
         "allocated_amount": "150.00", "allocated_tax": "0.00"},
    ]})
    assert r2.status_code == 200, r2.text
    ship2 = next(li for li in r2.json()["line_items"] if li["id"] == ship_line)
    assert ship2["non_po_fee"] is False           # unmark:标记已清
    assert ship2["non_po_note"] is None
```

- [ ] **Step 7: 跑整个 allocation 套件确认全绿(无回归)**

Run:
```bash
python -m pytest tests/test_invoice_allocations.py -v
```
Expected: 全部 PASS(原有 13 个 + 新增 3 个 = 16)。逐条确认新增 3 条 PASS,原有未变红。

- [ ] **Step 8: Commit**

```bash
git add epms-api/app/schemas/invoice.py epms-api/app/crud/invoice.py epms-api/tests/test_invoice_allocations.py
git commit -m "feat(invoice): exclude non-PO fee lines from PO matching (backend)

Add non_po_lines to the match request; match() writes non_po_fee/non_po_note
onto line_items (JSONB) and counts their pre-tax line_total toward the balance
so mixed invoices (goods on PO + shipping/handling with no PO line) can match.
Re-match is authoritative (clears stale marks = unmark). Zero migration.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 前端 — 类型与服务契约(`invoices.ts`)

**Files:**
- Modify: `epms/src/services/invoices.ts`(`InvoiceLineItem` 加字段;新增 `NonPoLineInput`;`MatchInvoiceBody` 加 `non_po_lines`)

**Interfaces:**
- Produces:`NonPoLineInput = { line_id: string; note?: string | null }`;`MatchInvoiceBody.non_po_lines?: NonPoLineInput[]`;`InvoiceLineItem.non_po_fee?: boolean`、`InvoiceLineItem.non_po_note?: string | null`。
- Consumes:后端 Task 1 的契约。

- [ ] **Step 1: 改 `InvoiceLineItem` 与新增 `NonPoLineInput`**

`epms/src/services/invoices.ts` —— `InvoiceLineItem` 加两字段:
```ts
export interface InvoiceLineItem {
  id?:          string
  description:  string
  quantity:     number
  unit:         string | null
  unit_price:   number
  line_total:   number
  non_po_fee?:  boolean          // marked as a non-PO fee (excluded from matching)
  non_po_note?: string | null
}
```

在 `AllocationInput` 之后新增:
```ts
export interface NonPoLineInput {
  line_id: string
  note?:   string | null
}
```

- [ ] **Step 2: 改 `MatchInvoiceBody`**

```ts
export interface MatchInvoiceBody {
  allocations?:  AllocationInput[]
  non_po_lines?: NonPoLineInput[]
  po_id?:        string
  gr_id?:        string
  gr_ids?:       string[]
  po_line_ids?:  string[]
}
```

- [ ] **Step 3: typecheck 不新增错误**

Run(在 `epms/` 下):
```bash
npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"
```
Expected: 计数 **≤ 59**(基线)。仅类型新增,不应引入新错误。

- [ ] **Step 4: Commit**

```bash
git add epms/src/services/invoices.ts
git commit -m "feat(invoice): non-PO fee types in invoice service contract

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 前端 — Allocation 面板右键标记 + 平账 + 调用点透传

**Files:**
- Modify: `epms/src/pages/invoices/InvoiceAllocationPanel.tsx`(右键菜单、mark/unmark、灰显、平账、prefill、payload)
- Modify: `epms/src/pages/invoices/MatchPanel.tsx:35-45`(`handleMatch` 接新 payload)
- Modify: `epms/src/pages/invoices/InvoiceListPage.tsx:326-332, 360-366`(`handleAllocSubmit` 接新 payload)

**Interfaces:**
- Produces:`InvoiceAllocationPanel` 的 `onSubmit` 签名改为
  `(payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[] }) => void`。
- Consumes:Task 2 的 `NonPoLineInput`;后端 Task 1 的 `non_po_lines` 请求字段。

> 无前端单测框架 → 每步验证 = `tsc`(不增错)+ `eslint`(改动文件不增错)+ 面板内手动冒烟(见 Step 6)。

- [ ] **Step 1: 面板 — imports、state、helpers、平账**

`epms/src/pages/invoices/InvoiceAllocationPanel.tsx`:

顶部 import 增加:
```tsx
import { useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '@/components/ui/button'
import { formatAmount } from '@/lib/utils'
import type { ApiInvoice, InvoiceLineItem, AllocationInput, NonPoLineInput } from '@/services/invoices'
import type { ApiPo } from '@/services/po'
```

改 `Props.onSubmit` 与新增 non-PO state(在 `dragLineId` state 附近):
```tsx
  onSubmit: (payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[] }) => void
```
```tsx
  // Prefill non-PO marks from persisted line_items (re-opening a matched/exception invoice).
  const [nonPo, setNonPo] = useState<Record<string, string | null>>(() => {
    const m: Record<string, string | null> = {}
    for (const l of lines) if (l.id && l.non_po_fee) m[l.id] = l.non_po_note ?? null
    return m
  })
  const [menu, setMenu] = useState<{ x: number; y: number; lineId: string } | null>(null)
```

在 `assignedTotal` 之后加被排除合计与 helpers:
```tsx
  const isNonPo = (lineId?: string | null) => !!lineId && lineId in nonPo
  const excludedTotal = useMemo(
    () => Object.keys(nonPo).reduce((s, lid) => s + allocatedByLine(lid), 0),
    [nonPo, lines],
  )
  const markNonPo = (lineId: string) => {
    clearLine(lineId)                                   // marking wins over any allocation
    setNonPo((p) => ({ ...p, [lineId]: p[lineId] ?? null }))
    setMenu(null)
  }
  const unmarkNonPo = (lineId: string) =>
    setNonPo((p) => { const n = { ...p }; delete n[lineId]; return n })
  const setNote = (lineId: string, note: string) =>
    setNonPo((p) => ({ ...p, [lineId]: note }))
```

平账改为扣掉被排除合计:
```tsx
  const unallocated = total - assignedTotal - excludedTotal
  const balanced = Math.abs(unallocated) < 0.01
```

新增提交组装(放在 `buildAllocations` 之后):
```tsx
  const submit = () => onSubmit({
    allocations: buildAllocations(),
    nonPoLines: Object.entries(nonPo).map(([line_id, note]) => ({ line_id, note })),
  })
```

- [ ] **Step 2: 面板 — 发票行渲染分叉(已标记 vs 可匹配)**

把发票行 `lines.map(...)`(现 96-115 行)整体替换为:
```tsx
            {lines.map((l) => {
              const a = l.id ? assign[l.id] : undefined
              if (isNonPo(l.id)) {
                return (
                  <div key={l.id}
                    className="rounded-lg border border-neutral-200 bg-neutral-100 px-3 py-2 text-sm opacity-80">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-neutral-500 line-through">{l.description}</span>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="rounded bg-neutral-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-neutral-600">
                          Non-PO fee
                        </span>
                        <span className="font-mono text-xs text-neutral-500">{formatAmount(Number(l.line_total), currency)}</span>
                      </div>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2">
                      <input
                        type="text"
                        value={(l.id && nonPo[l.id]) || ''}
                        onChange={(e) => l.id && setNote(l.id, e.target.value)}
                        placeholder="note (optional), e.g. shipping"
                        className="h-6 flex-1 rounded border border-neutral-300 bg-white px-2 text-[11px] focus:outline-none focus:ring-1 focus:ring-primary-500"
                      />
                      <button onClick={() => l.id && unmarkNonPo(l.id)}
                        className="text-[11px] text-primary-600 hover:underline">
                        Unmark
                      </button>
                    </div>
                  </div>
                )
              }
              return (
                <div key={l.id}
                  draggable
                  onDragStart={() => setDragLineId(l.id ?? null)}
                  onContextMenu={(e) => { e.preventDefault(); if (l.id) setMenu({ x: e.clientX, y: e.clientY, lineId: l.id }) }}
                  className={`cursor-grab rounded-lg border px-3 py-2 text-sm ${a ? 'border-primary-200 bg-primary-50' : 'border-neutral-200'} ${hintColor(Number(l.line_total)) ? `border-l-4 ${hintColor(Number(l.line_total))}` : ''}`}>
                  <div className="flex justify-between">
                    <span className="truncate">{l.description}</span>
                    <span className="font-mono text-xs">{formatAmount(Number(l.line_total), currency)}</span>
                  </div>
                  {a && (
                    <button onClick={() => l.id && clearLine(l.id)}
                      className="mt-1 text-[11px] text-primary-600 hover:underline">
                      Assigned - unassign
                    </button>
                  )}
                  <span className="mt-1 block text-[10px] text-neutral-300">right-click to mark as other fee</span>
                </div>
              )
            })}
```

- [ ] **Step 3: 面板 — 右键菜单浮层 + 提交按钮改用 `submit`**

把 `<Button onClick={() => onSubmit(buildAllocations())} ...>` 改为:
```tsx
        <Button onClick={submit} disabled={!balanced || submitting}>
```

在最外层 `</div>` 之前(return 的根节点内末尾)加菜单浮层:
```tsx
      {menu && createPortal(
        <div className="fixed inset-0 z-50"
          onClick={() => setMenu(null)}
          onContextMenu={(e) => { e.preventDefault(); setMenu(null) }}>
          <div className="absolute min-w-[210px] rounded-lg border border-neutral-200 bg-white py-1 shadow-lg"
            style={{ top: menu.y, left: menu.x }}
            onClick={(e) => e.stopPropagation()}>
            <button
              className="block w-full px-3 py-2 text-left text-sm text-neutral-700 hover:bg-neutral-50"
              onClick={() => markNonPo(menu.lineId)}>
              Mark as other fee (shipping etc.)
            </button>
          </div>
        </div>,
        document.body,
      )}
```

(可选)平账横条里,当 `excludedTotal > 0` 时补一行提示:
```tsx
          {excludedTotal > 0 && (
            <span className="text-[11px] text-neutral-400">
              {formatAmount(excludedTotal, currency)} in non-PO fees excluded from matching
            </span>
          )}
```

- [ ] **Step 4: `MatchPanel.tsx` — `handleMatch` 接新 payload**

`epms/src/pages/invoices/MatchPanel.tsx`:import 增补类型,并改 `handleMatch`:
```tsx
import type { ApiInvoice, AllocationInput, NonPoLineInput } from '@/services/invoices'
```
```tsx
  const handleMatch = (payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[] }) => {
    matchInvoiceMutation.mutate(
      { id: inv.id, allocations: payload.allocations, non_po_lines: payload.nonPoLines },
      {
        onSuccess: () => {
          onClose()
          navigate(`/invoices/${inv.id}`)
        },
      }
    )
  }
```

- [ ] **Step 5: `InvoiceListPage.tsx` — `handleAllocSubmit` 接新 payload**

`epms/src/pages/invoices/InvoiceListPage.tsx`:import 增补 `NonPoLineInput`,并改 `handleAllocSubmit`(现 326-332 行):
```tsx
import type { ApiInvoice, InvoiceLineItem, AllocationInput, NonPoLineInput } from '@/services/invoices'
```
```tsx
    const handleAllocSubmit = (payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[] }) => {
      const linkedGr = matchedPo ? grs.find((g) => g.po_id === matchedPo.id && g.status !== 'cancelled') : undefined
      matchInvoiceMutation.mutate(
        { id: createdInv.id, allocations: payload.allocations, non_po_lines: payload.nonPoLines, gr_id: linkedGr?.id },
        { onSuccess: () => onUploaded(createdInv.id) },
      )
    }
```

- [ ] **Step 6: 验证 — typecheck + lint + 手动冒烟**

Run(在 `epms/` 下):
```bash
npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"     # 期望 ≤ 59
npx eslint src/pages/invoices/InvoiceAllocationPanel.tsx src/pages/invoices/MatchPanel.tsx src/pages/invoices/InvoiceListPage.tsx
```
Expected: tsc 计数 ≤ 59;eslint 改动文件无**新**报错。

手动冒烟(`npm run dev`,开一张带 shipping 行、PO 缺该行的发票走 Match):
- 右键 goods 行 → 菜单出现;右键 shipping 行 → `Mark as other fee` → 该行灰显+删除线+`Non-PO fee` 徽章+备注输入框。
- goods 拖到 PO 行后,平账横条随 shipping 标记归零 → `Confirm allocation & match` 变可点。
- 点菜单外部/再次右键 → 菜单关闭;备注输入可编辑;`Unmark` 恢复为可拖拽行。
- 若某行已分配再右键标记 → 自动撤销其分配(标记优先)。
- 提交成功跳转 detail(徽章在 Task 4 显示)。

- [ ] **Step 7: Commit**

```bash
git add epms/src/pages/invoices/InvoiceAllocationPanel.tsx epms/src/pages/invoices/MatchPanel.tsx epms/src/pages/invoices/InvoiceListPage.tsx
git commit -m "feat(invoice): right-click to mark line as non-PO fee in allocation panel

Right-click an invoice line to mark it as a non-PO fee (shipping/handling):
the line greys out, leaves the drag pool, and its amount counts toward the
balance so Match PO unblocks. Optional inline note; Unmark reverses it;
marking a previously-allocated line clears the allocation. Portaled context
menu with click-outside. Threads non_po_lines through both call sites.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 前端 — 发票详情页显示「Non-PO fee」徽章

**Files:**
- Modify: `epms/src/pages/invoices/InvoiceDetailPage.tsx:528-536`(Line Items 表格行加徽章)

**Interfaces:**
- Consumes:`InvoiceLineItem.non_po_fee` / `non_po_note`(Task 2 的类型 + Task 1 的后端返回)。

- [ ] **Step 1: 在 Description 单元格加徽章 + 备注**

`epms/src/pages/invoices/InvoiceDetailPage.tsx` —— 把 Line Items 表体行(现 528-536)改为:
```tsx
                        {inv.line_items.map((item, idx) => (
                          <tr key={idx} className="border-b border-neutral-50 last:border-0">
                            <td className="py-2.5 pr-4 text-neutral-800">
                              <span>{item.description}</span>
                              {item.non_po_fee && (
                                <span className="ml-2 inline-flex items-center rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-neutral-500 align-middle">
                                  Non-PO fee
                                </span>
                              )}
                              {item.non_po_fee && item.non_po_note && (
                                <span className="ml-2 text-[11px] text-neutral-400">{item.non_po_note}</span>
                              )}
                            </td>
                            <td className="py-2.5 text-right font-mono text-xs text-neutral-600">{Number(item.quantity)}</td>
                            <td className="py-2.5 pl-3 text-xs text-neutral-400">{item.unit ?? '—'}</td>
                            <td className="py-2.5 text-right font-mono text-xs text-neutral-600">{formatAmount(Number(item.unit_price), inv.currency)}</td>
                            <td className="py-2.5 text-right font-mono text-xs font-semibold text-neutral-900">{formatAmount(Number(item.line_total), inv.currency)}</td>
                          </tr>
                        ))}
```

- [ ] **Step 2: 验证 — typecheck + lint + 手动**

Run(在 `epms/` 下):
```bash
npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"     # 期望 ≤ 59
npx eslint src/pages/invoices/InvoiceDetailPage.tsx
```
Expected: tsc ≤ 59;eslint 无新报错。手动:打开 Task 3 匹配过的发票 detail,shipping 行带 `Non-PO fee` 徽章与备注。

- [ ] **Step 3: Commit**

```bash
git add epms/src/pages/invoices/InvoiceDetailPage.tsx
git commit -m "feat(invoice): show Non-PO fee badge on invoice detail line items

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 右键标记 → Task 3 Step 3 菜单。✓
- 灰显+移出拖拽池+徽章+备注 → Task 3 Step 2。✓
- 标记与拖拽互斥、标记优先清分配 → Task 3 `markNonPo` 调 `clearLine`。✓
- 平账门禁扣除被标记合计 → 前端 Task 3 Step 1 / 后端 Task 1 Step 4。✓
- 持久化到 line_items JSONB(flag_modified)+ 预填 → 后端 Task 1 Step 4 / 前端 Task 3 Step 1。✓
- 后端 schema 扩展 + 422 校验 → Task 1 Steps 3-4。✓
- Detail 页徽章 → Task 4。✓
- finance_sync 不动、零迁移 → 计划内无相关改动。✓
- 可逆(unmark)+ re-match 权威清标记 → 后端 Task 1 Step 6 测试 / 前端 `unmarkNonPo`。✓
- 税:仅税前 line_total 计入 → 全程税前口径。✓

**范围外(已在 spec 标注,计划内不实现):** PA/付款不自动带非PO费用;`payment_execute` 掩盖逻辑不改。交付/发布说明需提醒制单人手填 PA 的 `shipping_amount`/`other_charges`。

**类型一致性:** 前后端字段名一致 —— 请求 `non_po_lines: [{ line_id, note }]`;行字段 `non_po_fee` / `non_po_note`;面板 `onSubmit({ allocations, nonPoLines })`;`NonPoLineInput = { line_id, note? }`。三处调用点(面板/MatchPanel/InvoiceListPage)签名一致。✓

**Placeholder 扫描:** 无 TBD/TODO;每步含可直接套用的真实代码。✓
