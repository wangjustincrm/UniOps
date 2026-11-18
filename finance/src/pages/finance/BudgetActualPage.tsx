/**
 * Budget vs Actual (预实对比) — per (cost center × income-expense item) budget /
 * actual / variance for the 5 expense categories, with tie-out rows for the
 * items finance tracks at category level (payroll, depreciation, shut-down
 * loss) and an exceptions panel for unmapped lines. Actual =
 * NC posted JV period debit (via /gl/budget-actual-grid); budget = budget-api
 * current approved plan. Read-only; each actual drills into its composing vouchers.
 */
import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { SortTh, useSortableRows, Input, TBody, TFoot, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn, currentMonthISO } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccountVouchersModal } from './AccountVouchersModal'
import { BudgetActualRollup, type Scope } from './BudgetActualRollup'
import { BudgetActualBreakdown } from './BudgetActualBreakdown'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-line-strong bg-surface px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

const CATEGORY_LABEL: Record<string, string> = {
  MOH: 'Manufacturing Overhead', RD: 'R&D', SELL: 'Selling', GA: 'G&A', FN: 'Financial',
}

interface DetailRow {
  cost_center_id: string | null; cost_center_code: string | null; cost_center_name: string | null
  income_expense_item_id: string | null; income_expense_code: string | null; income_expense_name: string | null
  budget: string; actual: string; variance: string
}
interface Category {
  account_code: string; category: string; detail: DetailRow[]
  payroll_actual: string; depreciation_actual: string
  // Every item tracked at category level rather than per cost center, from the
  // backend's own exclusion list — so adding one there adds a row here without
  // a change on this side. payroll_actual / depreciation_actual are the same
  // figures kept for compatibility.
  category_level?: { prefix: string; label: string; actual: string }[]
  detail_actual_total: string; category_actual_total: string; tie_ok: boolean
}
interface Unmapped {
  account_code: string; nc_cc_code: string | null
  income_expense_code: string | null; income_expense_name: string | null
  actual: string; line_count: number
}
interface GridResp { period: string; categories: Category[]; unmapped: Unmapped[] }

function money(v: string | number) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisMonth() { return currentMonthISO() }   // the plant's month, not UTC

interface Drill { accountCode: string; dimsValues?: string | null; title: string }

export default function BudgetActualPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [period, setPeriod] = useState(thisMonth())
  // Window and selection live here because the roll-up sets them and the
  // composition reads them — the two panels are one report, not two pages.
  const nowMonth = Number(thisMonth().slice(5, 7))
  const [win, setWin] = useState<[number, number]>([1, nowMonth])
  const [winLabel, setWinLabel] = useState('YTD')
  // No selection = no panel. The composition is an answer to a click, so it
  // should not be sitting open before anything has been asked.
  const [scope, setScope] = useState<Scope | null>(null)
  const [drill, setDrill] = useState<Drill | null>(null)
  const [jvId, setJvId] = useState<string | null>(null)
  // One basis for every panel on the page: a summary on one basis over a
  // composition on another would not add up to the row that was clicked.
  // Ticked by default: a month still being closed reads nearly empty on
  // tallied vouchers alone.
  const [includeUnposted, setIncludeUnposted] = useState(true)

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })

  const { data, isFetching } = useQuery({
    queryKey: ['budget-actual-grid', period, includeUnposted],
    queryFn: () => financeApi.get<GridResp>(
      `/gl/budget-actual-grid?period=${period}&include_unposted=${includeUnposted}`),
  })

  // The exceptions list: one plain row per account x NC cost center, whole.
  const unmappedSort = useSortableRows(data?.unmapped, {
    account: (u) => u.account_code, nc_cc: (u) => u.nc_cc_code,
    income_expense: (u) => u.income_expense_code, actual: (u) => Number(u.actual),
    lines: (u) => u.line_count,
  })
  const unmapped = unmappedSort.rows

  const onJvActed = () => {
    qc.invalidateQueries({ queryKey: ['account-balance'] })
    qc.invalidateQueries({ queryKey: ['ab-expand'] })
    qc.invalidateQueries({ queryKey: ['ab-vouchers'] })
    qc.invalidateQueries({ queryKey: ['budget-actual-grid'] })
  }

  const openDetailDrill = (accountCode: string, r: DetailRow) => {
    const parts = [`cost_center:${r.cost_center_id ?? 'none'}`]
    if (r.income_expense_item_id) parts.push(`income_expense_item:${r.income_expense_item_id}`)
    setDrill({
      accountCode, dimsValues: parts.join(','),
      title: `Vouchers — ${accountCode} · ${r.cost_center_code ?? 'no cost center'} · ${r.income_expense_code ?? '—'} · ${period}`,
    })
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/budget-actual"
      title="Budget vs Actual"
      subtitle={includeUnposted
        ? 'Budget (approved plan) vs NC actual incl. unposted vouchers, per cost center × income-expense item (CAD)'
        : 'Budget (approved plan) vs NC posted actual, per cost center × income-expense item (CAD)'}
    >
      {/* Room to scroll the last rows clear of the panel. The panel is sized
          against <main>; this is against the viewport, so it is a close-enough
          over-estimate rather than an exact match. */}
      <div className={cn('mx-auto max-w-7xl', scope && 'pb-[56vh]')}>
        {/* Summary first: this page is read by finance to report from, so the
            totals come before the composition. The detail grid below still
            works one month at a time — it answers "what is IN this figure",
            which is a different question from "what is the figure". */}
        <BudgetActualRollup fiscalYear={Number(period.slice(0, 4))}
                            onFiscalYearChange={(y) => setPeriod(`${y}${period.slice(4)}`)}
                            window={win}
                            onWindowChange={(w, label) => { setWin(w); setWinLabel(label) }}
                            scope={scope} onScopeChange={setScope}
                            includeUnposted={includeUnposted}
                            onIncludeUnpostedChange={setIncludeUnposted} />

        {scope && (
          <BudgetActualBreakdown fiscalYear={Number(period.slice(0, 4))}
                                 window={win} windowLabel={winLabel} scope={scope}
                                 includeUnposted={includeUnposted}
                                 onClose={() => setScope(null)} />
        )}

        {/* The month grid stays for the one thing the two panels above cannot
            do: open the vouchers behind a figure. It is a different question
            ("which documents") at a different grain (one month), so it is
            collapsed rather than removed. */}
        <details className="mb-4 rounded-lg border border-line bg-surface [&[open]>summary]:border-b [&[open]>summary]:border-line-subtle">
          <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold text-fg transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
            Monthly detail with vouchers
            <span className="ml-2 text-xs font-normal text-fg-muted">
              one month, drills into the documents behind each figure
            </span>
          </summary>
          <div className="px-3 py-3">
        <div className="mb-3 flex items-center gap-2">
          <Input type="month" value={period} onChange={(e) => setPeriod(e.target.value)}
                 className={cn(inputCls, 'w-40')} />
          <span className="text-xs text-fg-muted">
            Per cost centre × budget account, with a Vouchers drill on every row
          </span>
        </div>

        {isFetching && !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : (
          <div className="space-y-5">
            {(data?.categories ?? []).map((cat) => (
              <div key={cat.account_code} className="overflow-hidden rounded-lg border border-line">
                <div className="flex items-center justify-between bg-surface-subtle px-3 py-2">
                  <span className="text-sm font-semibold text-fg-secondary">
                    {CATEGORY_LABEL[cat.category] ?? cat.category} ({cat.account_code})
                  </span>
                  <span className={cn('rounded px-2 py-0.5 text-xs font-medium',
                    cat.tie_ok ? 'bg-success-soft text-success-fg' : 'bg-danger-soft text-danger-fg')}>
                    {cat.tie_ok ? 'Tied to NC' : 'Tie-out OFF'}
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <Table density="compact">
                    <THead>
                      <Tr className="text-xs text-fg-muted">
                        <Th>Cost Center</Th>
                        <Th>Income / Expense</Th>
                        <Th align="right">Budget</Th>
                        <Th align="right">Actual</Th>
                        <Th align="right">Variance</Th>
                        <Th className="w-20"></Th>
                      </Tr>
                    </THead>
                    <TBody>
                      {cat.detail.length === 0 && (
                        <Tr><Td align="center" colSpan={6} className="text-xs text-fg-muted">No actuals this period.</Td></Tr>
                      )}
                      {cat.detail.map((r, i) => (
                        <Tr key={`${r.cost_center_id ?? 'none'}-${r.income_expense_item_id ?? i}`}
                            className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                          <Td>
                            {r.cost_center_code
                              ? <><span className="font-mono text-xs">{r.cost_center_code}</span><span className="ml-1 text-fg-secondary">{r.cost_center_name}</span></>
                              : <span className="text-fg-muted">(no cost center)</span>}
                          </Td>
                          <Td>
                            {r.income_expense_code
                              ? <><span className="font-mono text-xs">{r.income_expense_code}</span><span className="ml-1 text-fg-secondary">{r.income_expense_name}</span></>
                              : <span className="text-fg-muted">—</span>}
                          </Td>
                          <Td align="right" className="font-mono text-fg-secondary">{money(r.budget)}</Td>
                          <Td align="right" className="font-mono">{money(r.actual)}</Td>
                          <Td className={cn('px-3 py-2 text-right font-mono',
                            Number(r.variance) < 0 && 'text-danger-fg')}>{money(r.variance)}</Td>
                          <Td align="right">
                            <Button variant="link" size="xs" onClick={() => openDetailDrill(cat.account_code, r)}>Vouchers</Button>
                          </Td>
                        </Tr>
                      ))}
                      {/* Category-level tie-out rows: finance tracks these by
                          category rather than by cost center. Driven by the
                          backend list — shut-down loss joined payroll and
                          depreciation there, and until it did, its 1.3M a month
                          sat in a detail row with no cost center and no name. */}
                      {(cat.category_level
                        ?? [{ prefix: 'CRM007', label: 'Payroll', actual: cat.payroll_actual },
                            { prefix: 'CRM004', label: 'Depreciation', actual: cat.depreciation_actual }])
                        .filter((x) => Number(x.actual) !== 0)
                        .map((x) => (
                          <Tr key={x.prefix} className="bg-surface-subtle text-fg-muted">
                            <Td className="italic" colSpan={2}>{x.label} (category-level)</Td>
                            <Td align="right">—</Td>
                            <Td align="right" className="font-mono">{money(x.actual)}</Td>
                            <Td align="right">—</Td>
                            <Td></Td>
                          </Tr>
                        ))}
                    </TBody>
                    <TFoot>
                      <Tr className="bg-surface-subtle font-semibold">
                        <Td colSpan={3}>Category total</Td>
                        <Td align="right" className="font-mono">{money(cat.category_actual_total)}</Td>
                        <Td colSpan={2}></Td>
                      </Tr>
                    </TFoot>
                  </Table>
                </div>
              </div>
            ))}

            {unmapped.length > 0 && (
              <div className="overflow-hidden rounded-lg border border-warning-line">
                <div className="bg-warning-soft px-3 py-2 text-sm font-semibold text-warning-fg">
                  Exceptions — {includeUnposted ? 'lines' : 'posted lines'} with no cost center ({unmapped.length}). Fix in NC or add to the mapping.
                </div>
                <Table density="compact">
                  <THead>
                    <Tr className="text-xs text-fg-muted">
                      <SortTh label="Account" sortKey="account" sort={unmappedSort.sort} onSort={unmappedSort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="NC Cost Center" sortKey="nc_cc" sort={unmappedSort.sort} onSort={unmappedSort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Income / Expense" sortKey="income_expense" sort={unmappedSort.sort} onSort={unmappedSort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Actual" sortKey="actual" sort={unmappedSort.sort} onSort={unmappedSort.onSort} align="right" className="px-3 py-2 font-medium" />
                      <SortTh label="Lines" sortKey="lines" sort={unmappedSort.sort} onSort={unmappedSort.onSort} align="right" className="px-3 py-2 font-medium" />
                      <Th className="w-20"></Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {unmapped.map((u, i) => (
                      <Tr key={`${u.account_code}-${u.nc_cc_code}-${i}`}
                          className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                        <Td className="font-mono text-xs">{u.account_code}</Td>
                        <Td className="font-mono text-xs">{u.nc_cc_code ?? '—'}</Td>
                        <Td>
                          {u.income_expense_code
                            ? <><span className="font-mono text-xs">{u.income_expense_code}</span><span className="ml-1 text-fg-secondary">{u.income_expense_name}</span></>
                            : <span className="text-fg-muted">—</span>}
                        </Td>
                        <Td align="right" className="font-mono">{money(u.actual)}</Td>
                        <Td align="right" className="font-mono text-fg-muted">{u.line_count}</Td>
                        <Td align="right">
                          <Button variant="link" size="xs" 
                                  onClick={() => setDrill({
                                    accountCode: u.account_code, dimsValues: 'cost_center:none',
                                    title: `Vouchers — ${u.account_code} · no cost center · ${period}`,
                                  })}>Vouchers</Button>
                        </Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </div>
            )}
          </div>
        )}
          </div>
        </details>
      </div>

      {drill && (
        <AccountVouchersModal accountCode={drill.accountCode} period={period}
                              dimsValues={drill.dimsValues} title={drill.title}
                              includeUnposted={includeUnposted}
                              onClose={() => setDrill(null)} onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={perms?.can_act ?? false} onClose={() => setJvId(null)} onActed={onJvActed} />
      )}
    </PortalChromeLayout>
  )
}
