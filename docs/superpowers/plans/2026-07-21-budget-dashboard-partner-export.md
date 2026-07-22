# Budget Dashboard 客商展开 XLSX 导出 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 EPMS Budget Dashboard 的 "Monthly Plan vs Actual" 表加 "Export XLSX" 按钮,导出当前 FY+cost center 下、所有预算科目按客商(客商=供应商/客户)展开的一张大表(Plan / NC 实际两指标,月度+年合计),由 finance-api 服务端 openpyxl 生成。

**Architecture:** 服务端生成:finance-api 新端点取 budget-api 月度 summary(Plan)+ 自身 NC 实际(逐科目、逐客商)→ openpyxl 组装 → `Response` 返回。前端原生 fetch+blob 触发下载。无后端数据模型改动、无新前端依赖。

**Tech Stack:** finance-api(FastAPI + SQLAlchemy async + openpyxl 3.1.5,已装)、budget-api(HTTP)、EPMS 前端(React + TS 5.9.3 + Vite)。

**Spec:** `docs/superpowers/specs/2026-07-21-budget-dashboard-partner-export-design.md`

**工作目录:** 当前 worktree `C:/Project/uniops-task-inbox`,分支 `feature/task-inbox-group-by-type`(与 task-inbox 分组同分支,一起发布)。所有改动在此。

---

## 验证策略(读一次)

- **finance-api**:有 pytest。
  - xlsx 生成器是**纯函数**(只依赖 openpyxl + stdlib)→ **TDD**:先写测试再实现,跑
    `cd finance-api && python -m pytest tests/test_predreal_export.py -q`。
    若 worktree 无 finance-api 运行环境:在 finance dev 容器内跑,或建最小 venv `pip install openpyxl pytest` 后在 `finance-api/` 目录跑(`app` 需可导入;`predreal_export.py` 只 import openpyxl,不引 app 内部重依赖)。
  - `budget_client.fetch_monthly_summary` / `crud.nc_partner_monthly_all` / 新路由:不写 DB 测试(避免依赖 finance-api 测试库);验证 = `python -m py_compile <file>` 无语法错 + 逐个 `python -c "import ..."` 冒烟 + 最后目视 QA。批量 crud 是现成 `nc_partner_monthly`(生产在用)的忠实镜像,风险低。
- **EPMS 前端**:无测试框架。类型检查(TS 5.9.3,**不带** `--ignoreDeprecations`):
  `cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -E 'BudgetDashboard|lib/api'` → 期望无输出(改动文件零新增错误;存量错误无关)。
- **目视 QA**(最后统一):起 dev,选 CC/FY 点导出,打开 xlsx 核对结构与几个数值对得上屏幕。

---

## 文件结构

| 层 | 新建 | 修改 |
|----|------|------|
| finance-api | `app/services/predreal_export.py`、`tests/test_predreal_export.py` | `app/services/budget_client.py`、`app/crud/account_balance.py`、`app/api/v1/account_balance.py` |
| epms 前端 | — | `src/lib/api.ts`、`src/pages/budget/BudgetDashboard.tsx` |

---

## Task 1: finance-api — xlsx 生成器(纯函数,TDD)

**Files:**
- Create: `finance-api/tests/test_predreal_export.py`
- Create: `finance-api/app/services/predreal_export.py`

- [ ] **Step 1: 先写失败测试**

Create `finance-api/tests/test_predreal_export.py`:
```python
import io

import openpyxl

from app.services.predreal_export import build_partner_export_xlsx


def _rows(data: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return list(wb.active.iter_rows(values_only=True))


def _find(rows, code, metric, vendor=None):
    for r in rows:
        if r and r[1] == code and r[4] == metric and (vendor is None or r[3] == vendor):
            return r
    raise AssertionError(f"row not found: {code} {metric} {vendor}")


def _sample():
    accounts = [
        {"account_id": "a1", "account_code": "5101010", "account_name": "Freight",
         "l1_code": "5101", "plan_by_month": {"1": "100.00", "2": "100.00"}, "plan_year": "1200.00"},
        {"account_id": "a2", "account_code": "6601010", "account_name": "Ads",
         "l1_code": "6601", "plan_by_month": {}, "plan_year": "0"},
    ]
    nc_monthly = {"a1": {1: "80.00", 2: "90.00"}}
    partners = {"a1": [
        {"partner_id": "p1", "partner_name": "ACME", "by_month": {1: "50.00", 2: "60.00"}, "year_total": "110.00"},
        {"partner_id": None, "partner_name": None, "by_month": {1: "30.00"}, "year_total": "30.00"},
    ]}
    return accounts, nc_monthly, partners


def test_header_and_structure():
    accounts, nc_monthly, partners = _sample()
    data = build_partner_export_xlsx(accounts=accounts, nc_monthly=nc_monthly,
        partners_by_account=partners, fiscal_year=2026, cost_center_label="All Cost Centers")
    rows = _rows(data)
    hdr = next(r for r in rows if r and r[0] == "Category")
    assert hdr[:5] == ("Category", "Account Code", "Account Name", "Vendor", "Metric")
    assert hdr[5] == "Jan" and hdr[16] == "Dec" and hdr[17] == "Year"


def test_account_plan_and_nc_and_vendors():
    accounts, nc_monthly, partners = _sample()
    data = build_partner_export_xlsx(accounts=accounts, nc_monthly=nc_monthly,
        partners_by_account=partners, fiscal_year=2026, cost_center_label="All Cost Centers")
    rows = _rows(data)
    plan = _find(rows, "5101010", "Plan", "(subtotal)")
    assert plan[5] == 100.0 and plan[17] == 1200.0
    nc = _find(rows, "5101010", "NC", "(subtotal)")
    assert nc[5] == 80.0 and nc[6] == 90.0 and nc[17] == 170.0   # NC year = sum of months
    vendors = {r[3] for r in rows if r and r[1] == "5101010" and r[4] == "NC" and r[3] != "(subtotal)"}
    assert "ACME" in vendors and "(no vendor)" in vendors


def test_grand_total():
    accounts, nc_monthly, partners = _sample()
    data = build_partner_export_xlsx(accounts=accounts, nc_monthly=nc_monthly,
        partners_by_account=partners, fiscal_year=2026, cost_center_label="All Cost Centers")
    rows = _rows(data)
    gt_plan = next(r for r in rows if r and r[0] == "Grand Total" and r[4] == "Plan")
    gt_nc = next(r for r in rows if r and r[0] == "Grand Total" and r[4] == "NC")
    assert gt_plan[17] == 1200.0    # a1 1200 + a2 0
    assert gt_nc[17] == 170.0       # only a1 had NC
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd finance-api && python -m pytest tests/test_predreal_export.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.predreal_export`(模块还没建)。

- [ ] **Step 3: 实现生成器**

Create `finance-api/app/services/predreal_export.py`:
```python
"""Server-side XLSX builder for the Budget Dashboard partner (客商) export.

Pure function: takes already-fetched data + returns .xlsx bytes. Only depends on
openpyxl + stdlib (no DB, no app-internal imports), so it is unit-testable without
a database. Layout: per budget account a Plan row + an NC row (subtotal), then one
NC row per vendor; 12 month columns + Year; Grand Total at the bottom.
"""
from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import openpyxl
from openpyxl.styles import Font

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONEY_FMT = "#,##0.00"
_MONEY_START_COL = 6  # 1-based: cols 1..5 are Category/Code/Name/Vendor/Metric


def _num(v: Any) -> float:
    """Coerce a money value (Decimal, str, int, None) to float. '' / None -> 0.0."""
    if v is None or v == "":
        return 0.0
    return float(Decimal(str(v)))


def _month(d: dict, m: int) -> float:
    """Read month `m` from a dict keyed by int (in-process) OR str (JSON)."""
    if m in d:
        return _num(d[m])
    return _num(d.get(str(m)))


def build_partner_export_xlsx(*, accounts: list[dict[str, Any]],
                              nc_monthly: dict[str, dict],
                              partners_by_account: dict[str, list[dict]],
                              fiscal_year: int, cost_center_label: str) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget vs Actual"
    bold = Font(bold=True)

    ws.append(["Budget vs Actual — Partner Breakdown"])
    ws["A1"].font = bold
    ws.append([f"FY {fiscal_year} · Cost Center: {cost_center_label}"])
    ws.append([])

    ws.append(["Category", "Account Code", "Account Name", "Vendor", "Metric",
               *_MONTHS, "Year"])
    for cell in ws[ws.max_row]:
        cell.font = bold

    gt_plan = [0.0] * 12
    gt_nc = [0.0] * 12
    gt_plan_year = 0.0
    gt_nc_year = 0.0

    def money_row(values: list, *, strong: bool = False) -> None:
        ws.append(values)
        r = ws.max_row
        for col in range(_MONEY_START_COL, len(values) + 1):
            ws.cell(row=r, column=col).number_format = _MONEY_FMT
        if strong:
            for cell in ws[r]:
                cell.font = bold

    ordered = sorted(accounts, key=lambda a: (a.get("l1_code", ""), a.get("account_code", "")))
    for a in ordered:
        aid = str(a["account_id"])
        code = a.get("account_code", "")
        name = a.get("account_name", "")
        l1 = a.get("l1_code", "")
        plan_bm = a.get("plan_by_month") or {}
        nc_bm = nc_monthly.get(aid) or {}

        plan_months = [_month(plan_bm, m) for m in range(1, 13)]
        plan_year = _num(a.get("plan_year"))
        nc_months = [_month(nc_bm, m) for m in range(1, 13)]
        nc_year = sum(nc_months)

        money_row([l1, code, name, "(subtotal)", "Plan", *plan_months, plan_year])
        money_row([l1, code, name, "(subtotal)", "NC", *nc_months, nc_year])

        for i in range(12):
            gt_plan[i] += plan_months[i]
            gt_nc[i] += nc_months[i]
        gt_plan_year += plan_year
        gt_nc_year += nc_year

        for p in partners_by_account.get(aid, []):
            pbm = p.get("by_month") or {}
            pmonths = [_month(pbm, m) for m in range(1, 13)]
            pname = p.get("partner_name") or "(no vendor)"
            money_row([l1, code, name, pname, "NC", *pmonths, sum(pmonths)])

    ws.append([])
    money_row(["Grand Total", "", "", "", "Plan", *gt_plan, gt_plan_year], strong=True)
    money_row(["Grand Total", "", "", "", "NC", *gt_nc, gt_nc_year], strong=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd finance-api && python -m pytest tests/test_predreal_export.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd C:/Project/uniops-task-inbox && git add finance-api/app/services/predreal_export.py finance-api/tests/test_predreal_export.py && git commit -m "feat(finance-api): xlsx builder for budget-actual partner export"
```

---

## Task 2: finance-api — budget-api 月度 summary 取数

**Files:**
- Modify: `finance-api/app/services/budget_client.py`

- [ ] **Step 1: 加 `fetch_monthly_summary`**

Append to `finance-api/app/services/budget_client.py` (module of free functions; uses existing `settings`, `httpx`, `_TIMEOUT`, `_auth_headers`, `Any`):
```python
async def fetch_monthly_summary(
    *, bearer_token: str | None, fiscal_year: int, cost_center_id: uuid.UUID | None,
) -> list[dict[str, Any]]:
    """Per-account monthly PLAN (+doc actual, ignored by plan-only callers) from
    budget-api. Returns response['accounts']: each dict has account_id,
    account_code, account_name, l1_code, plan_by_month{str->str}, plan_year, ...
    cost_center_id=None => aggregated across all cost centers. Requires auth on
    budget-api → forward the caller's bearer token."""
    params: dict[str, Any] = {"fiscal_year": fiscal_year}
    if cost_center_id is not None:
        params["cost_center_id"] = str(cost_center_id)
    url = f"{settings.budget_api_url}/api/v1/actuals/monthly-summary"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, params=params, headers=_auth_headers(bearer_token))
        r.raise_for_status()
        return r.json().get("accounts", [])
```

- [ ] **Step 2: 冒烟(语法+导入)**

Run:
```bash
cd finance-api && python -m py_compile app/services/budget_client.py && python -c "from app.services.budget_client import fetch_monthly_summary; print('ok')"
```
Expected: `ok`(若 `python -c` 因 app 依赖无法导入,至少 `py_compile` 必须无输出/成功;导入冒烟可留到容器/venv 里做)。

- [ ] **Step 3: Commit**

```bash
cd C:/Project/uniops-task-inbox && git add finance-api/app/services/budget_client.py && git commit -m "feat(finance-api): budget_client.fetch_monthly_summary (plan per account x month)"
```

---

## Task 3: finance-api — 批量客商取数 `nc_partner_monthly_all`

**Files:**
- Modify: `finance-api/app/crud/account_balance.py`

- [ ] **Step 1: 加批量函数**

In `finance-api/app/crud/account_balance.py`, add right AFTER the existing `nc_partner_monthly` function (uses existing `select`, `func`, `POSTED`, `JournalVoucher`, `JournalVoucherLine`, `Decimal`, `_ZERO`, `_s`, `_predreal_subtree`):
```python
async def nc_partner_monthly_all(db: AsyncSession, *, fiscal_year: int,
                                 cost_center_id=None) -> dict:
    """Bulk vendor (客商) breakdown for ALL predreal budget accounts in ONE query —
    the export equivalent of calling nc_partner_monthly per account. Groups posted
    JV debit by (income_expense_item_id, partner_id, partner_name, month). Returns
    {account_id_str: [ {partner_id, partner_name, by_month{month:str}, year_total}
    ... sorted by year_total desc ]}. partner_id None == '(no vendor)' bucket."""
    accts = await _predreal_subtree(db)
    month = func.substr(JournalVoucher.fiscal_period, 6, 2)
    q = (select(JournalVoucherLine.income_expense_item_id,
                JournalVoucherLine.partner_id, JournalVoucherLine.partner_name, month,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period.like(f"{fiscal_year}-%"),
                JournalVoucherLine.account_code.in_(accts),
                JournalVoucherLine.local_debit != 0))
    if cost_center_id is not None:
        q = q.where(JournalVoucherLine.cost_center_id == cost_center_id)
    q = q.group_by(JournalVoucherLine.income_expense_item_id,
                   JournalVoucherLine.partner_id, JournalVoucherLine.partner_name, month)

    by_acct: dict = {}
    for aid, pid, pname, mm, dr in (await db.execute(q)).all():
        if aid is None:
            continue
        agg = by_acct.setdefault(str(aid), {})
        key = str(pid) if pid else "__none__"
        rec = agg.setdefault(key, {"partner_id": str(pid) if pid else None,
                                   "partner_name": pname, "by_month": {}, "_total": _ZERO})
        d = Decimal(dr)
        rec["by_month"][int(mm)] = _s(d)
        rec["_total"] += d
        if pname and not rec["partner_name"]:
            rec["partner_name"] = pname

    out: dict = {}
    for aid, agg in by_acct.items():
        partners = sorted(agg.values(), key=lambda r: r["_total"], reverse=True)
        for r in partners:
            r["year_total"] = _s(r.pop("_total"))
        out[aid] = partners
    return out
```

- [ ] **Step 2: 冒烟**

Run:
```bash
cd finance-api && python -m py_compile app/crud/account_balance.py && python -c "from app.crud.account_balance import nc_partner_monthly_all; print('ok')"
```
Expected: `ok`(同上,`py_compile` 必须过)。

- [ ] **Step 3: Commit**

```bash
cd C:/Project/uniops-task-inbox && git add finance-api/app/crud/account_balance.py && git commit -m "feat(finance-api): nc_partner_monthly_all bulk vendor breakdown"
```

---

## Task 4: finance-api — 导出路由

**Files:**
- Modify: `finance-api/app/api/v1/account_balance.py`

- [ ] **Step 1: import 加 `Request`**

Change the fastapi import line (currently `from fastapi import APIRouter, Depends, HTTPException, Query`) to add `Request`:
```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request
```

- [ ] **Step 2: 加路由**

Append at the end of `finance-api/app/api/v1/account_balance.py`:
```python
@router.get("/budget-actual/partner-export")
async def budget_actual_partner_export(
    request: Request,
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    fiscal_year: int = Query(...),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    """Budget Dashboard export: one .xlsx with every predreal budget account
    expanded by vendor (客商). Plan (budget-api) + NC-posted actual, monthly + year.
    cost_center_id omitted => aggregated across all cost centers. Fail-open to
    plan=0 if budget-api is unreachable (mirrors /budget-actual-grid)."""
    import logging

    from fastapi.responses import Response
    from sqlalchemy import select as _select

    from app.models.mirrors import CostCenter
    from app.services import budget_client
    from app.services.predreal_export import build_partner_export_xlsx

    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else None

    try:
        accounts = await budget_client.fetch_monthly_summary(
            bearer_token=token, fiscal_year=fiscal_year, cost_center_id=cost_center_id)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "budget-api monthly-summary fetch failed; exporting with plan=0")
        accounts = []
    # Exclude payroll (CRM007) / depreciation (CRM004) — category-level only, same
    # as the dashboard's isPayrollOrDeprec filter.
    accounts = [a for a in accounts
                if not (str(a.get("account_code", "")).startswith("CRM004")
                        or str(a.get("account_code", "")).startswith("CRM007"))]

    nc = (await crud.nc_actuals_monthly(db, fiscal_year, cost_center_id))["accounts"]
    partners = await crud.nc_partner_monthly_all(
        db, fiscal_year=fiscal_year, cost_center_id=cost_center_id)

    if cost_center_id is not None:
        cc_name = (await db.execute(
            _select(CostCenter.name).where(CostCenter.id == cost_center_id))).scalar_one_or_none()
        cc_label = cc_name or str(cost_center_id)
    else:
        cc_label = "All Cost Centers"

    data = build_partner_export_xlsx(
        accounts=accounts, nc_monthly=nc, partners_by_account=partners,
        fiscal_year=fiscal_year, cost_center_label=cc_label)
    fname = f"budget-actual-FY{fiscal_year}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
```

- [ ] **Step 3: 冒烟**

Run:
```bash
cd finance-api && python -m py_compile app/api/v1/account_balance.py
```
Expected: 无输出(成功)。若有 finance-api 环境,再 `python -c "import app.api.v1.account_balance"` 确认导入无误。

- [ ] **Step 4: Commit**

```bash
cd C:/Project/uniops-task-inbox && git add finance-api/app/api/v1/account_balance.py && git commit -m "feat(finance-api): GET /gl/budget-actual/partner-export xlsx endpoint"
```

---

## Task 5: EPMS 前端 — finance-api 下载助手

**Files:**
- Modify: `epms/src/lib/api.ts`

- [ ] **Step 1: 加 `downloadFinanceFile`**

In `epms/src/lib/api.ts`, add right AFTER the existing `downloadCsv` function (reuses the in-file `getToken`, `FINANCE_BASE`, and the `Params` type that `downloadCsv` already uses):
```ts
/** Download a file (blob) from finance-api with auth, triggering a Save-As. */
export async function downloadFinanceFile(path: string, params?: Params, filename?: string): Promise<void> {
  const url = new URL(`${FINANCE_BASE}/finance/v1${path}`)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
    }
  }
  const token = getToken()
  const headers: HeadersInit = {}
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(url.toString(), { headers })
  if (!res.ok) throw new Error(`Export failed: ${res.statusText}`)

  const blob = await res.blob()
  const blobUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = blobUrl
  const cd = res.headers.get('content-disposition') ?? ''
  const match = cd.match(/filename="([^"]+)"/)
  a.download = filename ?? match?.[1] ?? 'export.xlsx'
  a.click()
  URL.revokeObjectURL(blobUrl)
}
```
Note: `FINANCE_BASE` is an absolute URL (`http://…:8004`), so `new URL(...)` needs no base arg (unlike `downloadCsv`, whose `BASE` can be relative `/api/v1`).

- [ ] **Step 2: 类型检查**

Run:
```bash
cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -E 'lib/api'
```
Expected: 无输出。

- [ ] **Step 3: Commit**

```bash
cd C:/Project/uniops-task-inbox && git add epms/src/lib/api.ts && git commit -m "feat(epms): downloadFinanceFile blob download helper"
```

---

## Task 6: EPMS 前端 — 导出按钮

**Files:**
- Modify: `epms/src/pages/budget/BudgetDashboard.tsx`

- [ ] **Step 1: import 下载助手**

Add near the top imports of `epms/src/pages/budget/BudgetDashboard.tsx`:
```tsx
import { downloadFinanceFile } from '@/lib/api'
```

- [ ] **Step 2: 加导出状态 + handler**

Inside `export default function BudgetDashboard()`, next to the other `useState` hooks (e.g. after `const [collapsedL1, setCollapsedL1] = useState<Set<string>>(new Set())`), add:
```tsx
const [exporting, setExporting] = useState(false)
const handleExport = async () => {
  setExporting(true)
  try {
    await downloadFinanceFile(
      '/gl/budget-actual/partner-export',
      { fiscal_year: fiscalYear, ...(ccId !== 'all' ? { cost_center_id: ccId } : {}) },
      `budget-actual-FY${fiscalYear}.xlsx`,
    )
  } catch {
    alert('Export failed. Please try again.')
  } finally {
    setExporting(false)
  }
}
```

- [ ] **Step 3: 加按钮**

In the "Monthly Plan vs Actual" `CardHeader`, the controls live in
`<div className="flex items-center gap-2 text-xs"> … Expand all · Collapse all … </div>`.
Add the export button as the FIRST child of that div, followed by a separator:
```tsx
<button type="button" onClick={handleExport} disabled={exporting}
  className="rounded-md border border-primary-300 bg-white px-2.5 py-1 font-medium text-primary-700 hover:bg-primary-50 disabled:opacity-50">
  {exporting ? 'Exporting…' : 'Export XLSX'}
</button>
<span className="text-neutral-300">·</span>
```
(So the row reads: `Export XLSX · Expand all · Collapse all`.)

- [ ] **Step 4: 类型检查**

Run:
```bash
cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -E 'BudgetDashboard'
```
Expected: 无输出。

- [ ] **Step 5: Commit**

```bash
cd C:/Project/uniops-task-inbox && git add epms/src/pages/budget/BudgetDashboard.tsx && git commit -m "feat(epms): Export XLSX button on Budget Dashboard monthly table"
```

---

## Task 7: 目视 QA + 收尾

**Files:** 无代码改动(除非发现 bug)。

- [ ] **Step 1: 后端整体冒烟**

Run(在有 finance-api 环境处;否则至少 py_compile 全绿):
```bash
cd finance-api && python -m pytest tests/test_predreal_export.py -q && \
  python -m py_compile app/services/budget_client.py app/crud/account_balance.py app/api/v1/account_balance.py app/services/predreal_export.py
```
Expected: 测试 PASS,py_compile 无错。

- [ ] **Step 2: 前端类型检查**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -E 'BudgetDashboard|lib/api'`
Expected: 无输出。

- [ ] **Step 3: 目视 QA(需起 dev + finance-api/budget-api 可达)**

在 Budget Dashboard(Finance 里)打开:
- "Monthly Plan vs Actual" 头部出现 "Export XLSX" 按钮;
- 选一个具体 cost center + FY → 点导出 → 下载 `budget-actual-FY<年>.xlsx`;
- 打开核对:表头 `Category|Account Code|Account Name|Vendor|Metric|Jan..Dec|Year`;每个科目有 Plan 行 + NC 行(subtotal),其下客商行(NC);"(no vendor)" 落位;数值与屏幕上该科目展开后的客商明细对得上;Grand Total 合计正确;
- 选 "All Cost Centers" 导出 → 得到跨 CC 汇总的同结构表,cc_label 显示 "All Cost Centers";
- budget-api 停掉再导一次(可选)→ 不 500,Plan 列为 0、NC 正常(fail-open)。

- [ ] **Step 4: 收尾**

两功能(task-inbox 分组 + budget 导出)在同一分支,统一按 superpowers:finishing-a-development-branch 走发布流程。**发布须遵循多会话纪律**:分支 merge 进 main → 全 15 镜像同一 sha 构建部署(**含 finance-api、epms 前端**;发布前先核实生产当前 TAG)。发布动作等用户明确指示。

---

## Self-Review(已核对)

**Spec 覆盖**:
- 按钮位置(§4.2)→ Task 6 ✓
- 服务端 openpyxl 生成(§3.3)→ Task 1 ✓
- budget-api 月度 summary 取 Plan(§3.1)→ Task 2 ✓
- 批量客商 NC(§3.2)→ Task 3 ✓
- 路由 + fail-open + payroll/deprec 排除 + cc_label(§3.4/§6)→ Task 4 ✓
- 前端下载助手(§4.1)→ Task 5 ✓
- 布局 Plan/NC 两指标 + 客商行 + Grand Total(§3.3)→ Task 1 生成器 ✓
- 权限仅登录(§1 决策表)→ Task 4 用 `CurrentUser`,无额外 scoping ✓
- 只要 Plan/NC、不要 Actual(docs)→ 生成器只出 Plan/NC 两行,取数忽略 actual_by_month ✓

**占位符扫描**:无 TBD/TODO;每步含完整代码/命令。

**类型/签名一致性**:
- `build_partner_export_xlsx(*, accounts, nc_monthly, partners_by_account, fiscal_year, cost_center_label)` 在 Task 1 定义、Task 4 调用一致。
- `nc_partner_monthly_all(db, *, fiscal_year, cost_center_id=None)` Task 3 定义、Task 4 调用一致(关键字参数)。
- `fetch_monthly_summary(*, bearer_token, fiscal_year, cost_center_id)` Task 2 定义、Task 4 调用一致。
- `nc_actuals_monthly` 输出 `{"accounts": {aid:{month:str}}}` — Task 4 取 `["accounts"]` 传给生成器 `nc_monthly`,月键为 int;生成器 `_month` 兼容 int/str,plan(str 键)与 nc(int 键)都能读 ✓。
- 金额:budget plan 为 Decimal-字符串、nc/partner 为 `_s` 字符串 → 生成器 `_num` 统一 `float(Decimal(str(v)))` ✓。

**已知取舍**:行集合以 budget-api 有预算/记录的科目为准(与 dashboard 一致);NC-only 无预算科目不单独成行。budget-api 挂掉时 fail-open 出 plan=0 空表而非报错。
