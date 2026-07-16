/**
 * Budget Dashboard — Plan vs Actual overview.
 *
 * Uses budget-api /actuals/summary to get per-account plan/committed/actual_spent
 * for a selected (cost_center, fiscal_year) scope. Replaces the old
 * /budget/summary aggregation that lived in epms-api.
 */
import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, TrendingUp, Building2, ChevronRight } from 'lucide-react'
import { cn, formatAmount, formatCADCompact } from '@/lib/utils'
import { Card, CardHeader } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useActualsSummary, useMonthlyActualsSummary, useAvailableFiscalYears } from '@/hooks/useBudget'
import { useConfig, useRolePermissions } from '@/hooks/useConfig'
import { useAuthStore } from '@/stores/auth.store'
import { useCostCenters } from '@/hooks/useCostCenters'
import type { ApiAccountSummary, ApiMonthlyAccountSummary } from '@/services/budget'

const currentYear = new Date().getUTCFullYear()

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const FULL_ACCESS_ROLES = new Set([
  'gm', 'opm', 'finance_manager', 'finance_bp', 'ap_clerk',
  'system_admin', 'cfo', 'auditor',
])

// Post-holder role codes that previously gated full access via the (now
// retired) company_config.role_management ids. Migrated to identity's
// user_roles — anyone holding one of these carries the role code in the
// permissions role union below. `vendor_manager` was never part of the
// old check and is intentionally excluded.
const SPECIAL_ROLE_CODES = new Set([
  'gm', 'opm', 'finance_manager', 'procurement_manager', 'finance_bp',
])

export default function BudgetDashboard() {
  const { data: config } = useConfig()
  const { data: myPermissions } = useRolePermissions()
  const { user } = useAuthStore()
  const { data: ccData } = useCostCenters({ active_only: true })
  const yearOptions = useAvailableFiscalYears()

  const costCenters = ccData ?? []
  const yellowThreshold = config?.budget_admin_config?.yellow_threshold_pct ?? 80
  const redThreshold    = config?.budget_admin_config?.red_threshold_pct ?? 100

  const myRoles = myPermissions?.roles ?? []
  const isSpecialRoleAssignee = myRoles.some((r) => SPECIAL_ROLE_CODES.has(r))
  const isFullAccess = !!user && (FULL_ACCESS_ROLES.has(user.role) || isSpecialRoleAssignee)

  const visibleCCs = isFullAccess
    ? costCenters
    : costCenters.filter((cc) => cc.department_id === user?.department_id)

  const [fiscalYear, setFiscalYear] = useState<number>(currentYear)
  const [ccId, setCcId] = useState<string>('all')

  const summaryParams = useMemo(() => ({
    fiscal_year: fiscalYear,
    ...(ccId !== 'all' ? { cost_center_id: ccId } : {}),
  }), [fiscalYear, ccId])

  const { data, isLoading } = useActualsSummary(summaryParams)
  const accounts: ApiAccountSummary[] = data?.accounts ?? []

  const { data: monthlyData, isLoading: monthlyLoading } = useMonthlyActualsSummary(summaryParams)
  const monthlyAccounts: ApiMonthlyAccountSummary[] = useMemo(() => monthlyData?.accounts ?? [], [monthlyData])

  const monthlyByL1 = useMemo(() => {
    const groups = new Map<string, { code: string; accounts: ApiMonthlyAccountSummary[] }>()
    for (const a of monthlyAccounts) {
      if (!groups.has(a.l1_code)) {
        groups.set(a.l1_code, { code: a.l1_code, accounts: [] })
      }
      groups.get(a.l1_code)!.accounts.push(a)
    }
    return Array.from(groups.values()).sort((a, b) => a.code.localeCompare(b.code))
  }, [monthlyAccounts])

  const monthlyGrandTotals = useMemo(() => {
    const plan: Record<number, number> = {}
    const actual: Record<number, number> = {}
    for (let m = 1; m <= 12; m++) { plan[m] = 0; actual[m] = 0 }
    let planYear = 0, actualYear = 0
    for (const a of monthlyAccounts) {
      for (let m = 1; m <= 12; m++) {
        plan[m] += Number(a.plan_by_month?.[m] ?? 0)
        actual[m] += Number(a.actual_by_month?.[m] ?? 0)
      }
      planYear += Number(a.plan_year)
      actualYear += Number(a.actual_year)
    }
    return { plan, actual, planYear, actualYear }
  }, [monthlyAccounts])

  const totalBudget    = accounts.reduce((s, a) => s + Number(a.annual_budget), 0)
  const totalCommitted = accounts.reduce((s, a) => s + Number(a.committed), 0)
  const totalSpent     = accounts.reduce((s, a) => s + Number(a.actual_spent), 0)
  const totalAvailable = totalBudget - totalCommitted - totalSpent
  const totalPct = totalBudget > 0 ? Math.round(((totalCommitted + totalSpent) / totalBudget) * 100) : 0

  const overBudget = accounts.filter((a) => a.utilisation_pct >= redThreshold)
  const nearBudget = accounts.filter((a) => a.utilisation_pct >= yellowThreshold && a.utilisation_pct < redThreshold)

  const scopeLabel = ccId === 'all'
    ? (isFullAccess ? 'Company-wide' : 'My Department')
    : (costCenters.find((cc) => cc.id === ccId)?.name ?? '')

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Budget Dashboard</h1>
          <p className="text-sm text-neutral-500 mt-0.5">
            Plan vs Actual · FY {fiscalYear}
            {scopeLabel && (
              <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-primary-50 border border-primary-200 px-2 py-0.5 text-xs text-primary-700 font-medium">
                <Building2 className="h-3 w-3" />
                {scopeLabel}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select value={fiscalYear} onChange={(e) => setFiscalYear(Number(e.target.value))}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600">
            {yearOptions.map((y) => <option key={y} value={y}>FY {y}</option>)}
          </select>
          {visibleCCs.length > 1 && (
            <select value={ccId} onChange={(e) => setCcId(e.target.value)}
              className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600">
              <option value="all">All Cost Centers</option>
              {visibleCCs.map((cc) => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
            </select>
          )}
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
            { label: 'Total Annual Budget', value: formatCADCompact(totalBudget), sub: 'Approved plan' },
            { label: 'Total Committed',     value: formatCADCompact(totalCommitted), sub: 'PA in-flight' },
            { label: 'Total Actual Spent',  value: formatCADCompact(totalSpent), sub: 'Paid & booked' },
            { label: 'Total Available',     value: formatCADCompact(totalAvailable), sub: `${totalPct}% utilised`, alert: totalAvailable < 0 },
          ].map((s) => (
            <Card key={s.label} className={cn('p-4', s.alert && 'ring-1 ring-danger-200')}>
              <p className="text-xs text-neutral-500">{s.label}</p>
              <p className={cn('text-xl font-bold mt-1 font-mono', s.alert ? 'text-danger-600' : 'text-neutral-900')}>{s.value}</p>
              <p className="text-xs text-neutral-400 mt-0.5">{s.sub}</p>
            </Card>
          ))}
        </div>
      )}

      {/* Over-budget alerts */}
      {overBudget.length > 0 && (
        <div className="rounded-xl border border-danger-200 bg-danger-50 p-4">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle className="h-4 w-4 text-danger-600 shrink-0" />
            <h2 className="text-sm font-semibold text-danger-700">
              {overBudget.length} account{overBudget.length !== 1 ? 's' : ''} at or over budget
            </h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {overBudget.map((a) => (
              <div key={a.account_id} className="rounded-md border border-danger-200 bg-white px-3 py-2 text-xs">
                <span className="font-mono font-semibold text-danger-700">{a.account_code}</span>
                <span className="text-neutral-500 mx-1">·</span>
                <span className="text-neutral-700">{a.account_name}</span>
                <span className="ml-2 font-semibold text-danger-600">{a.utilisation_pct}%</span>
                <div className="text-[10px] text-danger-500 mt-0.5">
                  Over by {formatAmount(-Number(a.available), 'CAD')}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Near-budget warnings */}
      {nearBudget.length > 0 && (
        <div className="rounded-xl border border-warning-200 bg-warning-50 p-4">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="h-4 w-4 text-warning-600 shrink-0" />
            <h2 className="text-sm font-semibold text-warning-700">
              {nearBudget.length} account{nearBudget.length !== 1 ? 's' : ''} approaching budget limit
            </h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {nearBudget.map((a) => (
              <div key={a.account_id} className="rounded-md border border-warning-200 bg-white px-3 py-2 text-xs">
                <span className="font-mono font-semibold text-warning-700">{a.account_code}</span>
                <span className="text-neutral-500 mx-1">·</span>
                <span className="text-neutral-700">{a.account_name}</span>
                <span className="ml-2 font-semibold text-warning-600">{a.utilisation_pct}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Monthly plan-vs-actual grid */}
      <Card className="overflow-hidden">
        <CardHeader>
          <h2 className="text-base font-semibold text-neutral-900">Monthly Plan vs Actual</h2>
          <p className="text-xs text-neutral-500 mt-0.5">
            Each cell: <span className="text-neutral-600 font-medium">plan</span> /{' '}
            <span className="text-primary-700 font-medium">actual</span> · actual over plan shown in red
            {ccId === 'all' && <span className="ml-2 text-neutral-400">· Aggregated across cost centers</span>}
          </p>
        </CardHeader>

        {monthlyLoading ? (
          <div className="py-12 text-center text-sm text-neutral-400">Loading monthly data…</div>
        ) : monthlyAccounts.length === 0 ? (
          <div className="py-12 text-center text-sm text-neutral-400">
            No approved plan for FY {fiscalYear} in this scope yet.{' '}
            <Link to="/budget/plans" className="text-primary-600 hover:underline">Create a plan →</Link>
          </div>
        ) : (
          <table className="text-sm w-full table-fixed border-collapse">
            <colgroup>
              <col className="w-[15%] min-w-[160px]" />
              {MONTHS.map((m) => <col key={m} />)}
              <col className="w-[8%]" />
            </colgroup>
            <thead>
              <tr className="border-b border-neutral-200">
                <th className="py-2.5 px-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">
                  Account
                </th>
                {MONTHS.map((m) => (
                  <th key={m} className="py-2.5 px-1.5 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">
                    {m}
                  </th>
                ))}
                <th className="py-2.5 px-2 text-right text-xs font-semibold uppercase tracking-wide text-neutral-700 bg-neutral-50">
                  Year
                </th>
              </tr>
            </thead>
            <tbody>
              {monthlyByL1.map((g) => (
                <MonthlyL1Group key={g.code} l1Code={g.code} accounts={g.accounts} />
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-neutral-300 bg-neutral-100">
                <td className="py-3 px-3 text-sm font-bold text-neutral-900 uppercase tracking-wide">
                  Total
                </td>
                {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                  <td key={m} className="py-3 px-1.5">
                    <PlanActualCell plan={monthlyGrandTotals.plan[m]} actual={monthlyGrandTotals.actual[m]} strong />
                  </td>
                ))}
                <td className="py-3 px-2 bg-neutral-200/60">
                  <PlanActualCell plan={monthlyGrandTotals.planYear} actual={monthlyGrandTotals.actualYear} strong />
                </td>
              </tr>
            </tfoot>
          </table>
        )}
      </Card>
    </div>
  )
}

// ── Monthly plan/actual cell ──────────────────────────────────────────────────

function PlanActualCell({ plan, actual, strong }: { plan: number; actual: number; strong?: boolean }) {
  const over = actual > plan && (plan > 0 || actual > 0)
  return (
    <div className="flex flex-col items-end leading-tight font-mono">
      <span className={cn('text-[11px] whitespace-nowrap text-neutral-500', strong && 'font-semibold text-neutral-600')}>
        {plan ? formatCADCompact(plan) : '–'}
      </span>
      <span className={cn(
        'text-[11px] whitespace-nowrap',
        over ? 'text-danger-600 font-semibold' : 'text-primary-700',
        strong && 'font-semibold',
      )}>
        {actual ? formatCADCompact(actual) : '–'}
      </span>
    </div>
  )
}

// ── L1 group with expandable monthly rows ─────────────────────────────────────

function MonthlyL1Group({ l1Code, accounts }: { l1Code: string; accounts: ApiMonthlyAccountSummary[] }) {
  const [expanded, setExpanded] = useState(true)

  const totals = useMemo(() => {
    const plan: Record<number, number> = {}
    const actual: Record<number, number> = {}
    for (let m = 1; m <= 12; m++) { plan[m] = 0; actual[m] = 0 }
    let planYear = 0, actualYear = 0
    for (const a of accounts) {
      for (let m = 1; m <= 12; m++) {
        plan[m] += Number(a.plan_by_month?.[m] ?? 0)
        actual[m] += Number(a.actual_by_month?.[m] ?? 0)
      }
      planYear += Number(a.plan_year)
      actualYear += Number(a.actual_year)
    }
    return { plan, actual, planYear, actualYear }
  }, [accounts])

  return (
    <>
      <tr className="border-b border-neutral-200 bg-neutral-50 cursor-pointer hover:bg-neutral-100" onClick={() => setExpanded((v) => !v)}>
        <td className="py-2.5 px-3 text-sm font-semibold text-neutral-800">
          <span className="font-mono text-xs text-neutral-500 mr-1">{l1Code}</span>
        </td>
        {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
          <td key={m} className="py-2.5 px-1.5">
            <PlanActualCell plan={totals.plan[m]} actual={totals.actual[m]} strong />
          </td>
        ))}
        <td className="py-2.5 px-2 bg-neutral-100">
          <PlanActualCell plan={totals.planYear} actual={totals.actualYear} strong />
        </td>
      </tr>
      {expanded && accounts.map((a) => (
        <tr key={a.account_id} className="border-b border-neutral-100 hover:bg-primary-50/50">
          <td className="py-2 px-3">
            <span className="font-mono text-xs text-neutral-500 mr-2">{a.account_code}</span>
            <span className="text-xs text-neutral-700 break-words">{a.account_name}</span>
          </td>
          {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
            <td key={m} className="py-2 px-1.5">
              <PlanActualCell plan={Number(a.plan_by_month?.[m] ?? 0)} actual={Number(a.actual_by_month?.[m] ?? 0)} />
            </td>
          ))}
          <td className="py-2 px-2 bg-neutral-50">
            <PlanActualCell plan={Number(a.plan_year)} actual={Number(a.actual_year)} strong />
          </td>
        </tr>
      ))}
    </>
  )
}
