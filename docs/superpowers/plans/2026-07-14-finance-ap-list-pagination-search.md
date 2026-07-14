# AP List 分页 + 搜索 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AP List 标准分页(50/页,`{total, items}` 信封)+ 搜索(Vendor Inv # / vendor 名)+ Hide exported 过滤挪服务端(否则与分页打架)。

**Architecture:** 完整复刻本库凭证中心列表范式(JV list:信封/offset/limit/ILIKE q/翻页清勾选)。设计已向用户口头确认(2026-07-14):后端 `GET /ap/invoices` 改信封+offset+`q`(ILIKE vendor_invoice_number/vendor_name)+`exported` bool 过滤,排序 `invoice_date desc, ap_invoice_number desc`;前端照 JournalVouchersPage 分页模式,hideExported 改传 `exported=false`。消费方仅 finance AP 页(已核,无测试读该 GET)。

**Tech Stack:** 同前。

## Global Constraints

- **分支** `feature/finance-jv-subsystem`;开工前 `git branch --show-current` 确认。
- **⚠️ 工作树有用户未提交 WIP**:只 `git add <指定文件>`,禁止 `-A`/`.`/`stash`/`reset`。
- **后端测试**:`cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <file> -q`;单进程串行。
- **前端 typecheck**:`cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`,显式 exit 0。
- UI 文案纯英文;金额 Number() 强转;翻页/筛选变化必须清勾选(JV 页教训)。

---

### Task 1: 后端 — 列表信封 + offset + q + exported

**Files:**
- Modify: `finance-api/app/crud/ap_invoice.py`(`list_invoices`,97-109 行)
- Modify: `finance-api/app/api/v1/ap_invoices.py`(list 端点)
- Test: `finance-api/tests/test_nc_ap_export.py`(追加——client/_h/_mk_ap 已在此文件)

**Interfaces:**
- Produces: `GET /finance/v1/ap/invoices?source&vendor_id&status&q&exported&limit&offset` → `{"total": int, "items": [InvoiceOut...]}`;`q` ILIKE `vendor_invoice_number`/`vendor_name`;`exported=true/false`(缺省不过滤,按 `nc_exported_at` 判空);`limit` 默认 50 上限 200;`offset` 默认 0 ≥0;排序 `invoice_date desc, ap_invoice_number desc`。crud 签名:`list_invoices(db, *, source=None, vendor_id=None, status=None, q=None, exported=None, limit=50, offset=0) -> tuple[int, list[ApInvoice]]`。

- [ ] **Step 1: 写失败测试**(追加到 tests/test_nc_ap_export.py 末尾)

```python
async def test_ap_list_pagination_and_search(client, db_session):
    for i in range(3):
        await _mk_ap(db_session, number=f"AP-2026-05{i:02d}",
                     vendor=("ACME Ltd" if i < 2 else "Beta Corp"), src_id=uuid.uuid4())
    # give one a vendor invoice number to search on
    from app.models.ap_invoice import ApInvoice
    target = (await db_session.execute(select(ApInvoice).where(
        ApInvoice.ap_invoice_number == "AP-2026-0500"))).scalar_one()
    target.vendor_invoice_number = "INV-XYZ-77"
    target.nc_exported_at = datetime.now(timezone.utc)
    await db_session.flush()

    r = await client.get("/finance/v1/ap/invoices?limit=2&offset=0", headers=_h())
    body = r.json()
    assert body["total"] == 3 and len(body["items"]) == 2
    r2 = await client.get("/finance/v1/ap/invoices?limit=2&offset=2", headers=_h())
    assert len(r2.json()["items"]) == 1

    rq = await client.get("/finance/v1/ap/invoices?q=INV-XYZ", headers=_h())
    assert [i["ap_invoice_number"] for i in rq.json()["items"]] == ["AP-2026-0500"]
    rv = await client.get("/finance/v1/ap/invoices?q=beta", headers=_h())
    assert rv.json()["total"] == 1 and rv.json()["items"][0]["vendor_name"] == "Beta Corp"

    re_ = await client.get("/finance/v1/ap/invoices?exported=false", headers=_h())
    assert re_.json()["total"] == 2
    rt = await client.get("/finance/v1/ap/invoices?exported=true", headers=_h())
    assert rt.json()["total"] == 1 and rt.json()["items"][0]["nc_exported_at"] is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py::test_ap_list_pagination_and_search -q`
Expected: FAIL(TypeError: list indices —— 现返回裸数组)

- [ ] **Step 3: crud 实现**(整函数替换;`func, or_` 进 import)

```python
async def list_invoices(db: AsyncSession, *, source: str | None = None,
                        vendor_id: uuid.UUID | None = None, status: str | None = None,
                        q: str | None = None, exported: bool | None = None,
                        limit: int = 50, offset: int = 0) -> tuple[int, list[ApInvoice]]:
    """Paginated envelope (total, page). `q` matches vendor_invoice_number /
    vendor_name; `exported` filters on nc_exported_at presence."""
    base = select(ApInvoice)
    if source:
        base = base.where(ApInvoice.source == source)
    if vendor_id:
        base = base.where(ApInvoice.vendor_id == vendor_id)
    if status:
        base = base.where(ApInvoice.status == status)
    if q:
        like = f"%{q}%"
        base = base.where(or_(ApInvoice.vendor_invoice_number.ilike(like),
                              ApInvoice.vendor_name.ilike(like)))
    if exported is True:
        base = base.where(ApInvoice.nc_exported_at.is_not(None))
    elif exported is False:
        base = base.where(ApInvoice.nc_exported_at.is_(None))
    total = (await db.execute(
        select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = list((await db.execute(
        base.order_by(ApInvoice.invoice_date.desc(), ApInvoice.ap_invoice_number.desc())
        .offset(offset).limit(limit))).scalars().all())
    return total, rows
```

- [ ] **Step 4: API 端点**(整函数替换;response_model 移除改手工信封,items 仍经 InvoiceOut)

```python
@router.get("/invoices")
async def list_invoices(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        source: str | None = Query(default=None),
                        vendor_id: uuid.UUID | None = Query(default=None),
                        status: str | None = Query(default=None),
                        q: str | None = Query(default=None),
                        exported: bool | None = Query(default=None),
                        limit: int = Query(default=50, le=200),
                        offset: int = Query(default=0, ge=0)):
    total, rows = await crud.list_invoices(
        db, source=source, vendor_id=vendor_id, status=status, q=q,
        exported=exported, limit=limit, offset=offset)
    return {"total": total,
            "items": [InvoiceOut.model_validate(r) for r in rows]}
```

- [ ] **Step 5: 跑测试确认通过 + 回归**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_ap_export.py tests/test_ap_invoice.py tests/test_ap_void_delete.py -q`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/crud/ap_invoice.py finance-api/app/api/v1/ap_invoices.py finance-api/tests/test_nc_ap_export.py
git commit -m "feat(finance): AP list pagination envelope + vendor/vendor-inv search + exported filter"
```

---

### Task 2: 前端 — 分页/搜索/服务端 exported 过滤

**Files:**
- Modify: `finance/src/pages/finance/AccountsPayablePage.tsx`

**Interfaces:**
- Consumes: Task 1 信封 `{total, items}` 与 `q/exported/limit/offset` 参数。

- [ ] **Step 1: 改造页面**(在现文件基础上,锚点式;整体行为对齐 JournalVouchersPage):

(a) 常量:`const PAGE_SIZE = 50`;新 state:

```tsx
  const [q, setQ] = useState('')
  const [qInput, setQInput] = useState('')
  const [page, setPage] = useState(0)
```

(b) `hideExported` 保留(默认 true)但**改为查询参数**;删除 `const visible = ...` 派生,列表直接用 `items`。

(c) 查询改信封:

```tsx
  interface ApListResp { total: number; items: ApInvoice[] }
  const { data, isFetching } = useQuery({
    queryKey: ['ap-invoices', source, statusFilter, q, hideExported, page],
    queryFn: () => {
      const qs = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) })
      if (source) qs.set('source', source)
      if (statusFilter) qs.set('status', statusFilter)
      if (q) qs.set('q', q)
      if (hideExported) qs.set('exported', 'false')
      return financeApi.get<ApListResp>(`/ap/invoices?${qs.toString()}`)
    },
  })
  const invoices = data?.items ?? []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
```

(d) 复位辅助 + 翻页清勾选(JV 页教训):

```tsx
  const resetPage = () => { setPage(0); setSelected(new Set()) }
  const gotoPage = (n: number) => { setPage(n); setSelected(new Set()) }
```

source/status select 的 onChange、Hide exported 的 onChange、搜索提交 都追加 `resetPage()`。

(e) filters 行加搜索表单(status select 后、Hide exported 前):

```tsx
              <form className="flex items-center gap-1"
                    onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); resetPage() }}>
                <input value={qInput} onChange={(e) => setQInput(e.target.value)}
                       placeholder="Vendor / vendor inv #…" className={cn(inputCls, 'w-52')} />
                <button type="submit" className="rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50">Search</button>
              </form>
```

(f) 表格下方加分页条(invoices tab 内、表格容器后):

```tsx
            <div className="mt-3 flex items-center justify-between text-sm text-neutral-500">
              <span>{total.toLocaleString()} invoice{total === 1 ? '' : 's'}</span>
              <div className="flex items-center gap-2">
                <button onClick={() => gotoPage(Math.max(0, page - 1))} disabled={page === 0}
                        className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50">Prev</button>
                <span>Page {page + 1} / {pageCount}</span>
                <button onClick={() => gotoPage(Math.min(pageCount - 1, page + 1))}
                        disabled={page >= pageCount - 1}
                        className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50">Next</button>
              </div>
            </div>
```

(g) `pickedCount`/`picked`/空态/`invoices.map` 均改基于新 `invoices`(=当前页 items);runExport 成功后 `resetPage()` 已由 invalidate+清勾选覆盖(保留现清勾选逻辑)。

- [ ] **Step 2: Typecheck**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0(显式确认)

- [ ] **Step 3: Commit**

```bash
cd c:/Project/uniops
git add finance/src/pages/finance/AccountsPayablePage.tsx
git commit -m "feat(finance-ui): AP list pagination + vendor/vendor-inv search + server-side exported filter"
```

---

## Self-Review 记录

- 覆盖:分页信封/offset/limit(T1)、搜索两字段(T1/T2)、exported 服务端(T1/T2)、排序稳定(T1)、翻页清勾选(T2)。
- 类型一致:`{total, items}`(T1→T2);`exported` bool 参数字符串 'false'(FastAPI bool 解析);InvoiceOut.model_validate(from_attributes 已配)。
- 取舍:ILIKE %/_ 不转义(与 JV list 同口径,台账已有该 Minor);vendor_id 参数保留(现无 UI 使用)。
