# Finance AP Invoice — 前端管理页 + Aging report Implementation Plan (Plan 5 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 epms 前端加 Finance 下的 **AP Invoices 管理页**（全量列表 + 过滤 source/vendor/status，消费 `GET /finance/v1/ap/invoices`）和 **AP Aging report 页**（消费 `GET /finance/v1/ap/aging`），作为 finance AP 真相源的统一查看入口。

**Architecture:** 新增 `financeApi` 客户端（lib/api.ts，base `${FINANCE_BASE}/finance/v1`，mirror 现有 `budgetApi`/`mdmApi`）+ `services/financeAp.ts` 类型与服务 + `hooks/useFinanceAp.ts`（TanStack Query）。两个页面 `src/pages/finance/ApInvoicesPage.tsx`、`ApAgingPage.tsx`，在 `App.tsx` 注册路由（自动套 `AppLayout` chrome），在 `Sidebar.tsx` 加 Finance 区两项（`view_finance` 门禁）。

**Tech Stack:** React + TS（epms 前端，TS 5.9.3）。typecheck：`npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 5.0`（项目有大量既有报错，标准=所改文件不新增错误）。UI 全英文；金额 `Number()` 强转再格式化。

**前置依赖：** Plan 1（finance `GET /ap/invoices`、`GET /ap/aging`）已上线（且 0014 已应用到共享库）。Plan 2-4 完成。

**本轮 Git 策略：** 不提交。Task 末「Checkpoint」只验证留工作区，**不要 git commit/push/branch/stash**。

**环境：** epms 前端容器（`uniops_epms_frontend`，vite HMR）。浏览器直连 finance-api（已发布 :8004），不走死代理。

---

## File Structure

- `epms/src/lib/api.ts` — 修改：加 `FINANCE_BASE` 常量 + `financeApi` 客户端（mirror budgetApi）。
- `epms/src/services/financeAp.ts` — 新建：`ApInvoice`/`ApAgingRow` 类型 + `financeApService`（listInvoices / aging）。
- `epms/src/hooks/useFinanceAp.ts` — 新建：`useApInvoices(filters)`、`useApAging()`。
- `epms/src/pages/finance/ApInvoicesPage.tsx` — 新建：列表 + 过滤。
- `epms/src/pages/finance/ApAgingPage.tsx` — 新建：账龄桶表。
- `epms/src/App.tsx` — 修改：注册两条路由。
- `epms/src/components/layout/Sidebar.tsx` — 修改：加 Finance 区两项（`view_finance` 门禁）。

类型口径（来自 Plan 1 finance 响应；金额均为字符串）：
- `GET /ap/invoices` → `ApInvoice[]`：`id, ap_invoice_number, source('epms'|'oa'), source_invoice_id, source_ref, vendor_id, vendor_name, vendor_invoice_number, amount, tax_amount, total_amount, paid_amount, currency, invoice_date, due_date, status, source_status, po_id, po_number`（query 参数：`source`、`vendor_id`、`status`、`limit`）。
- `GET /ap/aging` → `ApAgingRow[]`：`vendor_id, vendor_name, currency, current, d1_30, d31_60, d61_90, d90_plus, total`。

---

## Task 1: financeApi 客户端 + 服务/类型 + hooks

**Files:**
- Modify: `epms/src/lib/api.ts`
- Create: `epms/src/services/financeAp.ts`
- Create: `epms/src/hooks/useFinanceAp.ts`

- [ ] **Step 1: 加 FINANCE_BASE + financeApi（lib/api.ts）**

在 `EXPENSE_BASE` 定义旁加（mirror 现有 `MDM_BASE` 注释风格）：

```typescript
/** Finance microservice base URL (finance-api :8004). */
export const FINANCE_BASE = (import.meta.env.VITE_FINANCE_API_URL as string | undefined) || 'http://localhost:8004'
```

在 `mdmApi` 定义之后追加一个 finance 客户端（复制 `budgetRequest`/`budgetApi` 结构，仅改 base 与错误前缀）：

```typescript
// ── Finance-api client (separate base URL — :8004) ────────────────────────────

async function financeRequest<T>(
  method: string, path: string, body?: unknown, params?: Params,
): Promise<T> {
  const url = new URL(`${FINANCE_BASE}/finance/v1${path}`)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
    }
  }
  const hasBody = body !== undefined
  const token = getToken()
  const headers: Record<string, string> = {}
  if (hasBody) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(url.toString(), {
    method, headers, body: hasBody ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return undefined as T
  if (!res.ok) {
    let detail = res.statusText
    try {
      const err = await res.json()
      if (typeof err.detail === 'string') detail = err.detail
    } catch { /* ignore */ }
    throw new Error(`finance-api: ${detail}`)
  }
  return res.json() as Promise<T>
}

export const financeApi = {
  get:    <T>(path: string, params?: Params)       => financeRequest<T>('GET',    path, undefined, params),
  post:   <T>(path: string, body?: unknown)        => financeRequest<T>('POST',   path, body),
}
```

> 确认 `Params`、`getToken` 在 lib/api.ts 内已定义（budgetApi 用了它们）——直接复用。

- [ ] **Step 2: 服务类型 financeAp.ts**

新建 `epms/src/services/financeAp.ts`：

```typescript
import { financeApi } from '@/lib/api'

export type ApSource = 'epms' | 'oa'
export type ApStatus = 'draft' | 'posted' | 'partially_paid' | 'paid' | 'void'

export interface ApInvoice {
  id: string
  ap_invoice_number: string
  source: ApSource
  source_invoice_id: string
  source_ref: string | null
  vendor_id: string | null
  vendor_name: string | null
  vendor_invoice_number: string | null
  amount: string
  tax_amount: string
  total_amount: string
  paid_amount: string
  currency: string
  invoice_date: string
  due_date: string | null
  status: ApStatus
  source_status: string | null
  po_id: string | null
  po_number: string | null
}

export interface ApAgingRow {
  vendor_id: string | null
  vendor_name: string
  currency: string
  current: string
  d1_30: string
  d31_60: string
  d61_90: string
  d90_plus: string
  total: string
}

export interface ApInvoiceFilters {
  source?: ApSource
  vendor_id?: string
  status?: ApStatus
  limit?: number
}

export const financeApService = {
  listInvoices: (filters?: ApInvoiceFilters) =>
    financeApi.get<ApInvoice[]>('/ap/invoices', filters as Record<string, string | number | undefined>),
  aging: () => financeApi.get<ApAgingRow[]>('/ap/aging'),
}
```

- [ ] **Step 3: hooks useFinanceAp.ts**

新建 `epms/src/hooks/useFinanceAp.ts`：

```typescript
import { useQuery } from '@tanstack/react-query'
import { financeApService, type ApInvoiceFilters } from '@/services/financeAp'

export function useApInvoices(filters?: ApInvoiceFilters) {
  return useQuery({
    queryKey: ['ap-invoices', filters],
    queryFn: () => financeApService.listInvoices(filters),
    staleTime: 30_000,
  })
}

export function useApAging() {
  return useQuery({
    queryKey: ['ap-aging'],
    queryFn: () => financeApService.aging(),
    staleTime: 30_000,
  })
}
```

- [ ] **Step 4: typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 5.0 2>&1 | grep -E 'lib/api|financeAp|useFinanceAp' || echo "no errors in touched files"`
Expected: 无新错误。

- [ ] **Step 5: Checkpoint（不提交）**

---

## Task 2: ApInvoicesPage（列表 + 过滤）

**Files:**
- Create: `epms/src/pages/finance/ApInvoicesPage.tsx`

- [ ] **Step 1: 实现页面**

新建 `epms/src/pages/finance/ApInvoicesPage.tsx`（参照 BudgetDashboard 的导入/样式风格；金额 `Number()` 强转；全英文）：

```tsx
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { formatAmount, formatDate } from '@/lib/utils'
import { useApInvoices } from '@/hooks/useFinanceAp'
import type { ApSource, ApStatus } from '@/services/financeAp'

const SOURCES: (ApSource | '')[] = ['', 'epms', 'oa']
const STATUSES: (ApStatus | '')[] = ['', 'draft', 'posted', 'partially_paid', 'paid', 'void']

const STATUS_CLASS: Record<ApStatus, string> = {
  draft: 'bg-neutral-100 text-neutral-600',
  posted: 'bg-info-50 text-info-700',
  partially_paid: 'bg-warning-50 text-warning-700',
  paid: 'bg-success-50 text-success-700',
  void: 'bg-neutral-100 text-neutral-400 line-through',
}

export default function ApInvoicesPage() {
  const [source, setSource] = useState<ApSource | ''>('')
  const [status, setStatus] = useState<ApStatus | ''>('')
  const { data: invoices = [], isLoading } = useApInvoices({
    source: source || undefined,
    status: status || undefined,
    limit: 1000,
  })

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">AP Invoices</h1>
          <p className="mt-1 text-sm text-neutral-500">All payable invoices (EPMS + OA), owned by Finance.</p>
        </div>
        <Link to="/finance/ap-aging" className="text-sm text-primary-600 hover:underline">AP Aging report →</Link>
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <select value={source} onChange={(e) => setSource(e.target.value as ApSource | '')}
          className="rounded-lg border border-neutral-300 px-3 py-2 text-sm">
          {SOURCES.map((s) => <option key={s} value={s}>{s === '' ? 'All sources' : s.toUpperCase()}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value as ApStatus | '')}
          className="rounded-lg border border-neutral-300 px-3 py-2 text-sm">
          {STATUSES.map((s) => <option key={s} value={s}>{s === '' ? 'All statuses' : s.replace(/_/g, ' ')}</option>)}
        </select>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">
              <th className="px-4 py-3">AP No.</th>
              <th className="px-4 py-3">Source</th>
              <th className="px-4 py-3">Vendor</th>
              <th className="px-4 py-3">Vendor Inv #</th>
              <th className="px-4 py-3">Issue Date</th>
              <th className="px-4 py-3">Due Date</th>
              <th className="px-4 py-3 text-right">Total</th>
              <th className="px-4 py-3 text-right">Paid</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-neutral-400">Loading…</td></tr>
            ) : invoices.length === 0 ? (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-neutral-400">No AP invoices</td></tr>
            ) : invoices.map((inv) => (
              <tr key={inv.id} className="border-b border-neutral-100 last:border-0">
                <td className="px-4 py-3 font-mono text-xs">{inv.ap_invoice_number}</td>
                <td className="px-4 py-3 text-xs uppercase text-neutral-500">{inv.source}</td>
                <td className="px-4 py-3">{inv.vendor_name ?? '—'}</td>
                <td className="px-4 py-3 text-xs text-neutral-500">{inv.vendor_invoice_number ?? '—'}</td>
                <td className="px-4 py-3 text-xs">{formatDate(inv.invoice_date)}</td>
                <td className="px-4 py-3 text-xs">{inv.due_date ? formatDate(inv.due_date) : '—'}</td>
                <td className="px-4 py-3 text-right font-mono text-xs">{formatAmount(Number(inv.total_amount), inv.currency)}</td>
                <td className="px-4 py-3 text-right font-mono text-xs">{formatAmount(Number(inv.paid_amount), inv.currency)}</td>
                <td className="px-4 py-3">
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_CLASS[inv.status]}`}>
                    {inv.status.replace(/_/g, ' ')}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

> 校验 `formatAmount`/`formatDate` 从 `@/lib/utils` 导出（BudgetDashboard/InvoiceListPage 都用）。若 `STATUS_CLASS` 用到的色板类名（info-50 等）在 tailwind 配置里不存在，换成已有色板（参照 InvoiceListPage 的 STATUS_CFG 配色）。

- [ ] **Step 2: typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 5.0 2>&1 | grep -E 'ApInvoicesPage' || echo "no errors in ApInvoicesPage"`
Expected: 无错误。

- [ ] **Step 3: Checkpoint（不提交）**

---

## Task 3: ApAgingPage（账龄桶表）

**Files:**
- Create: `epms/src/pages/finance/ApAgingPage.tsx`

- [ ] **Step 1: 实现页面**

新建 `epms/src/pages/finance/ApAgingPage.tsx`：

```tsx
import { Link } from 'react-router-dom'
import { formatAmount } from '@/lib/utils'
import { useApAging } from '@/hooks/useFinanceAp'

const BUCKETS: { key: 'current' | 'd1_30' | 'd31_60' | 'd61_90' | 'd90_plus'; label: string }[] = [
  { key: 'current', label: 'Current' },
  { key: 'd1_30', label: '1–30' },
  { key: 'd31_60', label: '31–60' },
  { key: 'd61_90', label: '61–90' },
  { key: 'd90_plus', label: '90+' },
]

export default function ApAgingPage() {
  const { data: rows = [], isLoading } = useApAging()

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">AP Aging</h1>
          <p className="mt-1 text-sm text-neutral-500">Outstanding payables by vendor and aging bucket (by due date).</p>
        </div>
        <Link to="/finance/ap-invoices" className="text-sm text-primary-600 hover:underline">← AP Invoices</Link>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50 text-xs font-semibold uppercase tracking-wide text-neutral-500">
              <th className="px-4 py-3 text-left">Vendor</th>
              <th className="px-4 py-3 text-left">Currency</th>
              {BUCKETS.map((b) => <th key={b.key} className="px-4 py-3 text-right">{b.label}</th>)}
              <th className="px-4 py-3 text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-neutral-400">Loading…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={8} className="px-4 py-8 text-center text-neutral-400">No outstanding payables</td></tr>
            ) : rows.map((r, i) => (
              <tr key={`${r.vendor_id ?? 'none'}-${r.currency}-${i}`} className="border-b border-neutral-100 last:border-0">
                <td className="px-4 py-3">{r.vendor_name || '—'}</td>
                <td className="px-4 py-3 text-xs text-neutral-500">{r.currency}</td>
                {BUCKETS.map((b) => (
                  <td key={b.key} className="px-4 py-3 text-right font-mono text-xs">
                    {formatAmount(Number(r[b.key]), r.currency)}
                  </td>
                ))}
                <td className="px-4 py-3 text-right font-mono text-xs font-semibold">{formatAmount(Number(r.total), r.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 5.0 2>&1 | grep -E 'ApAgingPage' || echo "no errors in ApAgingPage"`
Expected: 无错误。

- [ ] **Step 3: Checkpoint（不提交）**

---

## Task 4: 路由 + 侧边栏导航 + 权限门禁

**Files:**
- Modify: `epms/src/App.tsx`
- Modify: `epms/src/components/layout/Sidebar.tsx`

- [ ] **Step 1: 注册路由（App.tsx）**

在 import 区加：
```tsx
import ApInvoicesPage from '@/pages/finance/ApInvoicesPage'
import ApAgingPage from '@/pages/finance/ApAgingPage'
```
在 `<Route element={<AppLayout />}>` 内（如 `/budget` 路由附近）加：
```tsx
            <Route path="/finance/ap-invoices" element={<ApInvoicesPage />} />
            <Route path="/finance/ap-aging" element={<ApAgingPage />} />
```

- [ ] **Step 2: 加 Finance 侧边栏区（Sidebar.tsx）**

先确认 `view_finance` 是否在前端 `RolePermissions` 类型里（`grep -n "view_finance" src/services/config.ts` 或类型定义处）。若有 → 用作 `permission`；若没有，改用一个已存在的 finance 权限键，或参照 BudgetDashboard 的 `FULL_ACCESS_ROLES` 思路（但 Sidebar 用的是 `permission?: keyof RolePermissions`，所以优先用已存在的 key；查到再定）。

在 `NAV_SECTIONS` 里加一个 Finance 区（放在 Invoices/PA 所在采购区之后、或独立区）：

```tsx
  {
    title: 'Finance',
    items: [
      { label: 'AP Invoices', href: '/finance/ap-invoices', icon: <FileText className="h-4 w-4" />, permission: 'view_finance' },
      { label: 'AP Aging', href: '/finance/ap-aging', icon: <TrendingUp className="h-4 w-4" />, permission: 'view_finance' },
    ],
  },
```

（`FileText` 已在 Sidebar 导入；`TrendingUp` 若未导入则从 `lucide-react` 补导入。`permission` 用上面确认的 key——若 `view_finance` 不是合法 `keyof RolePermissions`，TS 会报错，按 Step 1 查到的合法 key 替换。）

- [ ] **Step 3: typecheck（含路由/侧边栏）**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 5.0 2>&1 | grep -E 'App.tsx|Sidebar' || echo "no errors in App/Sidebar"`
Expected: 无新错误（注意 `permission` key 必须是合法 `keyof RolePermissions`）。

- [ ] **Step 4: Checkpoint（不提交）**

---

## Task 5: 验证（typecheck 全量 + 前端 HMR + 冒烟）

- [ ] **Step 1: 触改文件无新增 tsc 错误**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 5.0 2>&1 | grep -E 'finance/ApInvoicesPage|finance/ApAgingPage|financeAp|useFinanceAp|lib/api.ts|App.tsx|Sidebar.tsx'`
Expected: 无输出（这些文件零错误）。项目其它既有错误不在此列。

- [ ] **Step 2: 前端 HMR 生效**

Run: `docker logs uniops_epms_frontend --tail 8 | grep -iE 'hmr|update|ready|error'`
Expected: 看到对应文件的 hmr update，无编译 error。

- [ ] **Step 3: 冒烟（finance 直连可达）**

Run: `curl -s -o /dev/null -w "%{http_code}\n" --max-time 8 "http://localhost:8004/finance/v1/ap/invoices"`
Expected: 403（需鉴权 = 路由在、可达；非 404/000）。页面在浏览器登录态下应能加载（数据当前可能为空）。

- [ ] **Step 4: Checkpoint（不提交）**

---

## Final Verification（本计划）

- [ ] 触改的前端文件 typecheck 零新增错误。
- [ ] epms 前端 HMR 无编译错误。
- [ ] finance `/ap/invoices`、`/ap/aging` 浏览器直连可达（403 未鉴权）。
- [ ] 改动留工作区，不提交。

---

## 非目标 / 备注

- **Portal 导航**：本计划把 Finance 两项加到 **EPMS 自身 Sidebar**。Portal 应用的统一导航（[[project_uniops_portal_sidebar]]）如需也加这两项,属后续(portal navConfig)。
- AP 发票**详情页/钻取**、按 vendor 过滤的下拉（需 vendor 列表）等增强,本期未做(列表+状态/来源过滤 + aging 已满足 spec)。
- 行点击跳源系统详情(按 source 构造 epms/oa 链接)可作小增强,本期略。

## 验收对照（spec → 本计划）

| spec 需求 | 本计划 Task |
|---|---|
| Finance AP Invoice 管理页(list + 过滤 source/status) | Task 2 |
| Aging report(按 vendor+currency 桶,due date 基准) | Task 3 |
| finance-api 绝对 base URL(不走死代理) | Task 1 FINANCE_BASE |
| 入口(导航)指向 finance AP | Task 4 Sidebar |
| 英文 UI / Number() 强转 | Task 2/3 |
