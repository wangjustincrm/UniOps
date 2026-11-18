/**
 * Budget Dashboard — Plan vs Actual overview.
 *
 * Uses budget-api /actuals/summary for per-account plan and committed in a
 * selected (cost_center, fiscal_year) scope. Replaces the old /budget/summary
 * aggregation that lived in epms-api.
 *
 * Actual is NC posted plus what NC holds but has not tallied yet — the cards,
 * the cells and the over-budget marks — while "Include unposted vouchers" is
 * ticked (the default; a month still being closed reads nearly empty
 * otherwise), or NC posted alone when it is cleared. One flag drives every NC read on the page, drills and export too. budget-api's doc-side `actual_spent` is deliberately not shown: see
 * PlanActualCell.
 */
import { useState, useMemo, Fragment } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, TrendingUp, Building2, ChevronRight } from 'lucide-react'
import { cn, formatAmount, formatDate, todayISO } from '@/lib/utils'
import { CompactAmount } from '@/components/ui/CompactAmount'
import { Card, CardHeader } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useActualsSummary, useMonthlyActualsSummary, useAvailableFiscalYears, useNcActualsMonthly, useNcPartnerMonthly, useNcPartnerVouchers, useActualsScope } from '@/hooks/useBudget'
import { useConfig } from '@/hooks/useConfig'
import { useCostCenters } from '@/hooks/useCostCenters'
import type { ApiAccountSummary, ApiMonthlyAccountSummary } from '@/services/budget'
import { downloadFinanceFile } from '@/lib/api'

/** The plant's calendar year (company time zone), read when used. */
const currentYear = () => Number(todayISO().slice(0, 4))

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// Items the per-cost-center dashboard deliberately omits: Payroll (CRM007) and
// Depreciation (CRM004) are tracked at category level only, and shut-down loss
// (CRM09912 — the stop-production reclass of MOH into G&A) is not budgeted per
// cost center at all. Keep in lockstep with finance-api's EXCLUDED_IO_PREFIXES
// (crud/account_balance.py), which enforces the same list on the actual side.
const EXCLUDED_PREFIXES = ['CRM004', 'CRM007', 'CRM09912']
const isExcludedFromDashboard = (code: string) => EXCLUDED_PREFIXES.some((p) => code.startsWith(p))

export default function BudgetDashboard() {
  const { data: config } = useConfig()
  const { data: ccData } = useCostCenters({ active_only: true })
  const yearOptions = useAvailableFiscalYears()

  const costCenters = ccData ?? []
  const yellowThreshold = config?.budget_admin_config?.yellow_threshold_pct ?? 80
  const redThreshold    = config?.budget_admin_config?.red_threshold_pct ?? 100

  const { data: scope, isLoading: scopeLoading, isError: scopeError } = useActualsScope()
  const isFullAccess = scope?.full_access ?? false
  const visibleCCs = useMemo(() => {
    const scoped = scope?.cost_centers ?? []
    // Preserve the richer CostCenter objects from useCostCenters where available
    // (keeps existing dropdown label shape), falling back to the scope payload.
    const byId = new Map(costCenters.map((c) => [c.id, c]))
    return scoped.map((s) => byId.get(s.id) ?? {
      id: s.id, code: s.code, name: s.name, department_id: s.department_id,
    } as (typeof costCenters)[number])
  }, [scope, costCenters])

  const [fiscalYear, setFiscalYear] = useState<number>(currentYear())
  const [ccId, setCcId] = useState<string>('all')
  const [highlightId, setHighlightId] = useState<string | null>(null)
  const [collapsedL1, setCollapsedL1] = useState<Set<string>>(new Set())
  const [includeUnposted, setIncludeUnposted] = useState(true)
  const ncLabel = includeUnposted ? 'NC incl. unposted' : 'NC posted'
  const [exporting, setExporting] = useState(false)
  // A failed export, shown under the grid's header (was a browser alert).
  const [exportError, setExportError] = useState<string | null>(null)
  // Export only: follow each budget account with its vendor rows (the inline
  // drill, for every account at once). Does not change what the grid shows.
  const [exportByVendor, setExportByVendor] = useState(false)
  const handleExport = async () => {
    setExporting(true)
    setExportError(null)
    try {
      await downloadFinanceFile(
        '/gl/budget-actual/partner-export',
        {
          fiscal_year: fiscalYear,
          ...(ccId !== 'all' ? { cost_center_id: ccId } : {}),
          ...(includeUnposted ? { include_unposted: true } : {}),
          ...(exportByVendor ? { expand_vendor: true } : {}),
        },
        `budget-actual-FY${fiscalYear}${exportByVendor ? '-by-vendor' : ''}.xlsx`,
      )
    } catch {
      setExportError('Export failed. Please try again.')
    } finally {
      setExporting(false)
    }
  }

  const summaryParams = useMemo(() => ({
    fiscal_year: fiscalYear,
    ...(ccId !== 'all' ? { cost_center_id: ccId } : {}),
  }), [fiscalYear, ccId])

  const { data, isLoading } = useActualsSummary(summaryParams)
  const accounts: ApiAccountSummary[] = (data?.accounts ?? []).filter((a) => !isExcludedFromDashboard(a.account_code))

  const { data: monthlyData, isLoading: monthlyLoading } = useMonthlyActualsSummary(summaryParams)
  const monthlyAccounts: ApiMonthlyAccountSummary[] = useMemo(
    () => (monthlyData?.accounts ?? []).filter((a) => !isExcludedFromDashboard(a.account_code)), [monthlyData])

  // NC posted actual (finance-api) per account × month — the second cell line.
  const { data: ncData } = useNcActualsMonthly({ ...summaryParams, include_unposted: includeUnposted })
  const ncByAccount = useMemo(() => ncData?.accounts ?? {}, [ncData])

  const monthlyByL1 = useMemo(() => {
    const groups = new Map<string, { code: string; accounts: ApiMonthlyAccountSummary[] }>()
    for (const a of monthlyAccounts) {
      if (!groups.has(a.l1_code)) {
        groups.set(a.l1_code, { code: a.l1_code, accounts: [] })
      }
      groups.get(a.l1_code)!.accounts.push(a)
    }
    // sort accounts within each group by code (budget-api order isn't guaranteed)
    for (const g of groups.values()) {
      g.accounts.sort((a, b) => a.account_code.localeCompare(b.account_code))
    }
    return Array.from(groups.values()).sort((a, b) => a.code.localeCompare(b.code))
  }, [monthlyAccounts])

  // L1 group expand/collapse is controlled by the parent (for expand-all /
  // collapse-all + auto-expand on alert jump). collapsedL1 holds collapsed codes.
  const l1ByAccount = useMemo(() => {
    const out: Record<string, string> = {}
    for (const a of monthlyAccounts) out[a.account_id] = a.l1_code
    return out
  }, [monthlyAccounts])
  const allL1Codes = useMemo(() => monthlyByL1.map((g) => g.code), [monthlyByL1])
  const expandAll = () => setCollapsedL1(new Set())
  const collapseAll = () => setCollapsedL1(new Set(allL1Codes))
  const toggleL1 = (code: string) => setCollapsedL1((s) => {
    const n = new Set(s)
    if (n.has(code)) n.delete(code); else n.add(code)
    return n
  })

  // Click an alert card -> expand its L1 group, scroll to the row, flash it amber.
  const jumpToAccount = (id: string) => {
    const l1 = l1ByAccount[id]
    if (l1) setCollapsedL1((s) => { const n = new Set(s); n.delete(l1); return n })
    setHighlightId(id)
    window.setTimeout(() => {
      document.getElementById(`ba-row-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 60)
    window.setTimeout(() => setHighlightId((h) => (h === id ? null : h)), 2600)
  }

  const monthlyGrandTotals = useMemo(() => {
    const plan: Record<number, number> = {}
    const nc: Record<number, number> = {}
    for (let m = 1; m <= 12; m++) { plan[m] = 0; nc[m] = 0 }
    let planYear = 0, ncYear = 0
    for (const a of monthlyAccounts) {
      for (let m = 1; m <= 12; m++) {
        plan[m] += Number(a.plan_by_month?.[m] ?? 0)
        const n = Number(ncByAccount[a.account_id]?.[m] ?? 0)
        nc[m] += n; ncYear += n
      }
      planYear += Number(a.plan_year)
    }
    return { plan, nc, planYear, ncYear }
  }, [monthlyAccounts, ncByAccount])

  const totalBudget    = accounts.reduce((s, a) => s + Number(a.annual_budget), 0)
  const totalCommitted = accounts.reduce((s, a) => s + Number(a.committed), 0)
  // Actual Spent follows NC posted (the final actual), not doc-side actual_spent.
  const totalNcSpent = Object.values(ncByAccount).reduce(
    (s, months) => s + Object.values(months).reduce((t, v) => t + Number(v), 0), 0)
  const totalAvailable = totalBudget - totalCommitted - totalNcSpent
  const totalPct = totalBudget > 0 ? Math.round(((totalCommitted + totalNcSpent) / totalBudget) * 100) : 0

  // over-budget alerts judged on NC posted (the final actual): per account,
  // NC year total vs annual budget.
  const ncYearByAccount = useMemo(() => {
    const out: Record<string, number> = {}
    for (const [aid, months] of Object.entries(ncByAccount)) {
      out[aid] = Object.values(months).reduce((s, v) => s + Number(v), 0)
    }
    return out
  }, [ncByAccount])
  const accountsNc = useMemo(() => accounts.map((a) => {
    const ncYear = ncYearByAccount[a.account_id] ?? 0
    const budget = Number(a.annual_budget)
    const nc_util = budget > 0 ? Math.round((ncYear / budget) * 100) : (ncYear > 0 ? 999 : 0)
    return { account_id: a.account_id, account_code: a.account_code, account_name: a.account_name,
             nc_util, nc_over: ncYear - budget }
  }), [accounts, ncYearByAccount])
  const overBudget = accountsNc.filter((a) => a.nc_util >= redThreshold)
  const nearBudget = accountsNc.filter((a) => a.nc_util >= yellowThreshold && a.nc_util < redThreshold)

  // A Director can be responsible for several departments, so the chip must not
  // hard-code the singular.
  const scopedDeptCount = new Set(
    (scope?.cost_centers ?? []).map((cc) => cc.department_id).filter(Boolean),
  ).size
  const scopeLabel = ccId === 'all'
    ? (isFullAccess ? 'Company-wide' : (scopedDeptCount > 1 ? 'My Departments' : 'My Department'))
    : (costCenters.find((cc) => cc.id === ccId)?.name ?? '')

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-fg">Budget Dashboard</h1>
          <p className="text-sm text-fg-muted mt-0.5">
            Plan vs Actual · FY {fiscalYear}
            {scopeLabel && (
              <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-primary-50 border border-primary-200 px-2 py-0.5 text-xs text-primary-700 font-medium">
                <Building2 className="h-3 w-3" />
                {scopeLabel}
              </span>
            )}
          </p>
          {!scopeLoading && !scopeError && scope && !scope.full_access && scope.cost_centers.length === 0 && (
            <p className="text-sm text-warning-fg mt-1">
              No budget is visible for your account. Contact your administrator if this is unexpected.
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <select value={fiscalYear} onChange={(e) => setFiscalYear(Number(e.target.value))}
            className="h-10 rounded-md border border-line-strong bg-surface px-3 text-sm text-fg-secondary focus:outline-none focus:ring-2 focus:ring-primary-600">
            {yearOptions.map((y) => <option key={y} value={y}>FY {y}</option>)}
          </select>
          {(visibleCCs.length > 1 || (!isFullAccess && visibleCCs.length >= 1)) && (
            <select value={ccId} onChange={(e) => setCcId(e.target.value)}
              className="h-10 rounded-md border border-line-strong bg-surface px-3 text-sm text-fg-secondary focus:outline-none focus:ring-2 focus:ring-primary-600">
              <option value="all">All Cost Centers</option>
              {visibleCCs.map((cc) => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
            </select>
          )}
          <label className="inline-flex cursor-pointer select-none items-center gap-1.5 text-sm text-fg-secondary"
                 title="Also count vouchers entered in NC but not yet tallied (记账). Discarded or deleted vouchers never count.">
            <input type="checkbox" checked={includeUnposted}
                   onChange={(e) => setIncludeUnposted(e.target.checked)}
                   className="h-4 w-4 cursor-pointer rounded border-line-strong accent-primary-600" />
            Include unposted vouchers
          </label>
          <Link to="/budget/plans" className="inline-flex items-center gap-1 text-sm text-primary-600 hover:underline">
            Manage Plans <ChevronRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      </div>

      {/* Summary cards */}
      {isLoading ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[
            { label: 'Total Annual Budget', value: totalBudget, sub: 'Approved plan' },
            { label: 'Total Committed',     value: totalCommitted, sub: 'PA in-flight' },
            { label: 'Total Actual Spent',  value: totalNcSpent, sub: ncLabel },
            { label: 'Total Available',     value: totalAvailable, sub: `${totalPct}% utilised`, alert: totalAvailable < 0 },
          ].map((s) => (
            <Card key={s.label} className={cn('p-4', s.alert && 'ring-1 ring-danger-line')}>
              <p className="text-xs text-fg-muted">{s.label}</p>
              <p className={cn('text-xl font-bold mt-1 font-mono', s.alert ? 'text-danger-fg' : 'text-fg')}><CompactAmount value={s.value} /></p>
              <p className="text-xs text-fg-muted mt-0.5">{s.sub}</p>
            </Card>
          ))}
        </div>
      )}

      {/* Over-budget alerts */}
      {overBudget.length > 0 && (
        <div className="rounded-xl border border-danger-line bg-danger-soft p-4">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle className="h-4 w-4 text-danger-fg shrink-0" />
            <h2 className="text-sm font-semibold text-danger-fg">
              {overBudget.length} account{overBudget.length !== 1 ? 's' : ''} over budget ({ncLabel})
            </h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {overBudget.map((a) => (
              <button type="button" key={a.account_id} onClick={() => jumpToAccount(a.account_id)}
                className="rounded-md border border-danger-line bg-surface px-3 py-2 text-xs text-left transition-colors hover:border-danger-400 hover:bg-danger-soft"
                title="Jump to this account in the table">
                <span className="font-mono font-semibold text-danger-fg">{a.account_code}</span>
                <span className="text-fg-muted mx-1">·</span>
                <span className="text-fg-secondary">{a.account_name}</span>
                <span className="ml-2 font-semibold text-danger-fg">{a.nc_util}%</span>
                <div className="text-2xs text-danger-fg mt-0.5">
                  Over by {formatAmount(a.nc_over, 'CAD')}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Near-budget warnings */}
      {nearBudget.length > 0 && (
        <div className="rounded-xl border border-warning-line bg-warning-soft p-4">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="h-4 w-4 text-warning-fg shrink-0" />
            <h2 className="text-sm font-semibold text-warning-fg">
              {nearBudget.length} account{nearBudget.length !== 1 ? 's' : ''} approaching budget limit ({ncLabel})
            </h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {nearBudget.map((a) => (
              <button type="button" key={a.account_id} onClick={() => jumpToAccount(a.account_id)}
                className="rounded-md border border-warning-line bg-surface px-3 py-2 text-xs text-left transition-colors hover:border-warning-400 hover:bg-warning-soft"
                title="Jump to this account in the table">
                <span className="font-mono font-semibold text-warning-fg">{a.account_code}</span>
                <span className="text-fg-muted mx-1">·</span>
                <span className="text-fg-secondary">{a.account_name}</span>
                <span className="ml-2 font-semibold text-warning-fg">{a.nc_util}%</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Monthly plan-vs-actual grid */}
      <Card className="overflow-hidden">
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-base font-semibold text-fg">Monthly Plan vs Actual</h2>
            <div className="flex items-center gap-2 text-xs">
              <label className="inline-flex cursor-pointer select-none items-center gap-1 text-fg-secondary"
                     title="In the exported file, list each budget account's NC actual by vendor under the account row">
                <input type="checkbox" checked={exportByVendor} disabled={exporting}
                       onChange={(e) => setExportByVendor(e.target.checked)}
                       className="h-3.5 w-3.5 cursor-pointer rounded border-line-strong accent-primary-600" />
                Expand vendor
              </label>
              <button type="button" onClick={handleExport} disabled={exporting}
                className="rounded-md border border-primary-300 bg-surface px-2.5 py-1 font-medium text-primary-700 hover:bg-primary-50 disabled:opacity-50">
                {exporting ? 'Exporting…' : 'Export XLSX'}
              </button>
              <span className="text-fg-faint">·</span>
              <button type="button" onClick={expandAll} className="text-primary-600 hover:underline">Expand all</button>
              <span className="text-fg-faint">·</span>
              <button type="button" onClick={collapseAll} className="text-primary-600 hover:underline">Collapse all</button>
            </div>
          </div>
          <p className="text-xs text-fg-muted mt-0.5">
            Each cell: <span className="text-fg-secondary font-medium">plan</span> /{' '}
            <span className="text-success-fg font-medium">{ncLabel} actual</span> · over budget in red
            {ccId === 'all' && <span className="ml-2 text-fg-muted">· Aggregated across cost centers</span>}
          </p>
          {exportError && <p role="alert" className="mt-1 text-xs text-danger-fg">{exportError}</p>}
        </CardHeader>

        {monthlyLoading ? (
          <div className="py-12 text-center text-sm text-fg-muted">Loading monthly data…</div>
        ) : monthlyAccounts.length === 0 ? (
          <div className="py-12 text-center text-sm text-fg-muted">
            No approved plan for FY {fiscalYear} in this scope yet.{' '}
            <Link to="/budget/plans" className="text-primary-600 hover:underline">Create a plan →</Link>
          </div>
        ) : (
          <div className="max-h-[70vh] overflow-auto">
          <table className="text-sm w-full table-fixed border-collapse">
            <colgroup>
              <col className="w-[15%] min-w-[160px]" />
              {MONTHS.map((m) => <col key={m} />)}
              <col className="w-[8%]" />
            </colgroup>
            <thead>
              <tr>
                <th className="sticky top-0 z-20 border-b-2 border-line-strong bg-surface-strong py-2.5 px-3 text-left text-xs font-semibold uppercase tracking-wide text-fg-muted">
                  Account
                </th>
                {MONTHS.map((m) => (
                  <th key={m} className="sticky top-0 z-20 border-b-2 border-line-strong bg-surface-strong py-2.5 px-1.5 text-right text-xs font-semibold uppercase tracking-wide text-fg-muted">
                    {m}
                  </th>
                ))}
                <th className="sticky top-0 z-20 border-b-2 border-line-strong bg-surface-strong py-2.5 px-2 text-right text-xs font-semibold uppercase tracking-wide text-fg-secondary">
                  Year
                </th>
              </tr>
            </thead>
            <tbody>
              {monthlyByL1.map((g) => (
                <MonthlyL1Group key={g.code} l1Code={g.code} accounts={g.accounts} ncByAccount={ncByAccount}
                  fiscalYear={fiscalYear} costCenterId={ccId !== 'all' ? ccId : undefined} highlightId={highlightId}
                  includeUnposted={includeUnposted}
                  expanded={!collapsedL1.has(g.code)} onToggle={() => toggleL1(g.code)} />
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td className="sticky bottom-0 z-20 border-t-2 border-fg-faint bg-surface-strong py-3 px-3 text-sm font-bold text-fg uppercase tracking-wide">
                  Total
                </td>
                {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                  <td key={m} className="sticky bottom-0 z-20 border-t-2 border-fg-faint bg-surface-strong py-3 px-1.5">
                    <PlanActualCell plan={monthlyGrandTotals.plan[m]} nc={monthlyGrandTotals.nc[m]} strong />
                  </td>
                ))}
                <td className="sticky bottom-0 z-20 border-t-2 border-fg-faint bg-line-strong py-3 px-2">
                  <PlanActualCell plan={monthlyGrandTotals.planYear} nc={monthlyGrandTotals.ncYear} strong />
                </td>
              </tr>
            </tfoot>
          </table>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Monthly plan/actual cell ──────────────────────────────────────────────────

// Two lines, not three. The middle one used to show budget-api's doc-side
// `actual_spent`, which sounds like "what this cost centre has spent in UniOps"
// and is not: budget_ledger has never been written by anything but the one-off
// opening-balance import (15,828 `opening` rows, 2026-07-07 — the commit /
// release / actualize entry points have no callers anywhere in the repo). So
// the line showed an opening figure in every month's cell, under a label that
// promised current spend. A number that is always wrong is worse than a number
// that is absent; removed until something actually books to that ledger.
function PlanActualCell({ plan, nc, strong }: { plan: number; nc?: number; strong?: boolean }) {
  // over-budget is judged on NC posted (the final actual)
  const over = nc !== undefined && nc > plan && (plan > 0 || nc > 0)
  return (
    <div className="flex flex-col items-end leading-tight font-mono">
      <span className={cn('text-2xs whitespace-nowrap text-fg-muted', strong && 'font-semibold text-fg-secondary')}>
        {plan ? <CompactAmount value={plan} hint="Plan" /> : '–'}
      </span>
      {nc !== undefined && (
        <span className={cn(
          'text-2xs whitespace-nowrap',
          over ? 'text-danger-fg font-bold' : 'text-success-fg',
          strong && 'font-semibold',
        )}>
          {nc ? <CompactAmount value={nc} hint="NC actual (red = over budget)" /> : '–'}
        </span>
      )}
    </div>
  )
}

// ── L1 group with expandable monthly rows ─────────────────────────────────────

function MonthlyL1Group({ l1Code, accounts, ncByAccount, fiscalYear, costCenterId, highlightId, includeUnposted, expanded, onToggle }: {
  l1Code: string; accounts: ApiMonthlyAccountSummary[]
  ncByAccount: Record<string, Record<number, string>>
  fiscalYear: number; costCenterId?: string; highlightId?: string | null
  includeUnposted: boolean
  expanded: boolean; onToggle: () => void
}) {
  const [expandedAcct, setExpandedAcct] = useState<string | null>(null)

  const totals = useMemo(() => {
    const plan: Record<number, number> = {}
    const nc: Record<number, number> = {}
    for (let m = 1; m <= 12; m++) { plan[m] = 0; nc[m] = 0 }
    let planYear = 0, ncYear = 0
    for (const a of accounts) {
      for (let m = 1; m <= 12; m++) {
        plan[m] += Number(a.plan_by_month?.[m] ?? 0)
        const n = Number(ncByAccount[a.account_id]?.[m] ?? 0)
        nc[m] += n; ncYear += n
      }
      planYear += Number(a.plan_year)
    }
    return { plan, nc, planYear, ncYear }
  }, [accounts, ncByAccount])

  const ncYearFor = (accountId: string) =>
    Object.values(ncByAccount[accountId] ?? {}).reduce((s, v) => s + Number(v), 0)

  return (
    <>
      <tr className="border-t-2 border-line-strong bg-surface-sunken cursor-pointer hover:bg-surface-strong" onClick={onToggle}>
        <td className="py-2.5 px-3 text-sm font-semibold text-fg">
          <span className="mr-1 text-2xs text-fg-muted">{expanded ? '▾' : '▸'}</span>
          <span className="font-mono text-xs text-fg-muted">{l1Code}</span>
        </td>
        {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
          <td key={m} className="py-2.5 px-1.5">
            <PlanActualCell plan={totals.plan[m]} nc={totals.nc[m]} strong />
          </td>
        ))}
        <td className="py-2.5 px-2 bg-surface-strong">
          <PlanActualCell plan={totals.planYear} nc={totals.ncYear} strong />
        </td>
      </tr>
      {expanded && accounts.map((a) => {
        const isOpen = expandedAcct === a.account_id
        return (
          <Fragment key={a.account_id}>
            <tr id={`ba-row-${a.account_id}`}
                className={cn('border-b border-line-subtle transition-colors hover:bg-primary-50/60',
                  highlightId === a.account_id ? 'bg-warning-soft' : 'bg-surface')}>
              <td className="py-2 px-3">
                <button type="button"
                  onClick={() => setExpandedAcct((id) => (id === a.account_id ? null : a.account_id))}
                  className="flex items-start gap-1 text-left hover:underline"
                  title="Break down NC actual by vendor/customer">
                  <span className="mt-0.5 w-3 shrink-0 text-2xs text-fg-muted">{isOpen ? '▾' : '▸'}</span>
                  <span>
                    <span className="font-mono text-xs text-fg-muted mr-2">{a.account_code}</span>
                    <span className="text-xs text-primary-700 break-words">{a.account_name}</span>
                  </span>
                </button>
              </td>
              {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                <td key={m} className="py-2 px-1.5">
                  <PlanActualCell plan={Number(a.plan_by_month?.[m] ?? 0)}
                    nc={Number(ncByAccount[a.account_id]?.[m] ?? 0)} />
                </td>
              ))}
              <td className="py-2 px-2 bg-surface-subtle">
                <PlanActualCell plan={Number(a.plan_year)} nc={ncYearFor(a.account_id)} strong />
              </td>
            </tr>
            {isOpen && <PartnerRows account={a} fiscalYear={fiscalYear} costCenterId={costCenterId}
                                    includeUnposted={includeUnposted} />}
          </Fragment>
        )
      })}
    </>
  )
}

// ── Partner (客商/供应商/客户) inline breakdown ────────────────────────────────

// What an unattributed row is called, by why it is unattributed. "(no vendor)"
// for all of them hid the difference between "the voucher names several parties
// and we refuse to guess" and "this is an internal accrual with no party at all".
const NO_VENDOR_LABEL: Record<string, string> = {
  multi: '(several parties on the voucher)',
  none: '(no vendor — internal entry)',
  line: '(no vendor)',
  voucher: '(no vendor)',
  mixed: '(no vendor)',
}

function PartnerRows({ account, fiscalYear, costCenterId, includeUnposted }: {
  account: ApiMonthlyAccountSummary
  fiscalYear: number
  costCenterId?: string
  includeUnposted: boolean
}) {
  const { data, isLoading } = useNcPartnerMonthly({
    income_expense_item_id: account.account_id, fiscal_year: fiscalYear, cost_center_id: costCenterId,
    include_unposted: includeUnposted,
  })
  const partners = data?.partners ?? []
  const [voucherKey, setVoucherKey] = useState<string | null>(null)  // `${partnerId}:${month}`

  if (isLoading) {
    return <tr><td colSpan={14} className="bg-success-soft py-2 pl-10 pr-3 text-xs text-fg-muted">Loading vendor breakdown…</td></tr>
  }
  if (partners.length === 0) {
    return <tr><td colSpan={14} className="bg-success-soft py-2 pl-10 pr-3 text-xs text-fg-muted">No NC actuals for this item in the current scope.</td></tr>
  }
  return (
    <>
      {partners.map((p, i) => {
        const pid = p.key
        return (
          <Fragment key={`${pid}-${i}`}>
            <tr className="border-b border-line-subtle bg-success-soft">
              <td className="py-1.5 pl-10 pr-3 text-xs text-fg-secondary break-words">
                {p.partner_name || <span className="text-fg-muted">{NO_VENDOR_LABEL[p.source]}</span>}
                {/* NC hangs the vendor on the payable line, not the expense
                    line, so most of these are read off the voucher rather than
                    off the line the money is on. Say so — a vendor that is not
                    on the line in NC must not look like one that is. */}
                {(p.source === 'voucher' || p.source === 'mixed') && p.partner_name && (
                  <span className="ml-1.5 rounded bg-success-soft px-1 py-0.5 text-2xs text-success-fg"
                        title={p.source === 'voucher'
                          ? 'Vendor taken from the voucher (the expense line itself names none)'
                          : `Partly from the voucher: ${p.inferred_total} of ${p.year_total}`}>
                    {p.source === 'voucher' ? 'from voucher' : 'partly from voucher'}
                  </span>
                )}
              </td>
              {Array.from({ length: 12 }, (_, k) => k + 1).map((m) => {
                const v = Number(p.by_month[m] ?? 0)
                const key = `${pid}:${m}`
                return (
                  <td key={m} className="py-1.5 px-1.5 text-right">
                    {v ? (
                      <button className={cn('font-mono text-2xs hover:underline',
                        voucherKey === key ? 'font-semibold text-success-fg' : 'text-success-fg')}
                        onClick={() => setVoucherKey((k) => (k === key ? null : key))}>
                        <CompactAmount value={v} />
                      </button>
                    ) : <span className="text-2xs text-fg-faint">–</span>}
                  </td>
                )
              })}
              <td className="py-1.5 px-2 text-right font-mono text-2xs font-semibold bg-success-soft"><CompactAmount value={Number(p.year_total)} /></td>
            </tr>
            {voucherKey && voucherKey.startsWith(`${pid}:`) && (
              <VoucherRow account={account} fiscalYear={fiscalYear} costCenterId={costCenterId}
                includeUnposted={includeUnposted}
                partnerKey={p.key} partnerName={p.partner_name || NO_VENDOR_LABEL[p.source]}
                month={Number(voucherKey.split(':')[1])} />
            )}
          </Fragment>
        )
      })}
    </>
  )
}

function VoucherRow({ account, fiscalYear, costCenterId, includeUnposted, partnerKey, partnerName, month }: {
  account: ApiMonthlyAccountSummary
  fiscalYear: number
  costCenterId?: string
  includeUnposted: boolean
  partnerKey: string
  partnerName: string | null
  month: number
}) {
  const { data, isLoading } = useNcPartnerVouchers({
    income_expense_item_id: account.account_id, fiscal_year: fiscalYear, month,
    cost_center_id: costCenterId, partner_id: partnerKey, include_unposted: includeUnposted,
  })
  const rows = data?.rows ?? []
  return (
    <tr>
      <td colSpan={14} className="border-l-4 border-success-400 bg-surface-sunken px-10 py-2">
        <div className="mb-1 text-2xs font-semibold text-fg-secondary">
          Vouchers · {partnerName || '(no vendor)'} · {MONTHS[month - 1]} {fiscalYear}
        </div>
        {isLoading ? (
          <div className="text-xs text-fg-muted">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="text-xs text-fg-muted">No vouchers.</div>
        ) : (
          <table className="w-full text-2xs">
            <thead>
              <tr className="text-fg-muted">
                <th className="px-2 py-1 text-left font-medium">Date</th>
                <th className="px-2 py-1 text-left font-medium">Voucher</th>
                <th className="px-2 py-1 text-left font-medium">Account</th>
                <th className="px-2 py-1 text-left font-medium">Summary</th>
                <th className="px-2 py-1 text-right font-medium">Debit</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.jv_id}-${i}`} className="border-t border-line-subtle">
                  <td className="px-2 py-1 font-mono text-fg-secondary">{formatDate(r.voucher_date)}</td>
                  <td className="px-2 py-1 font-mono">
                    {r.jv_number}
                    {r.posted === false && (
                      <span className="ml-1.5 rounded bg-warning-soft px-1.5 py-0.5 font-sans text-2xs font-medium text-warning-fg"
                            title="Entered in NC, not yet tallied">
                        Unposted
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1 font-mono text-fg-secondary">{r.account_code}</td>
                  <td className="px-2 py-1 text-fg-secondary">{r.summary || '—'}</td>
                  <td className="px-2 py-1 text-right font-mono"><CompactAmount value={Number(r.local_debit)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </td>
    </tr>
  )
}
