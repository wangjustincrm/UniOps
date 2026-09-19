/**
 * Budget-vs-Actual roll-up — the summary finance reports from, above the detail.
 *
 * Four levels, two of them trees over the same cost centres: by expense centre
 * (the cost-centre code prefix) and by department. A department can span expense
 * centres — Supply Chain runs G&A, manufacturing overhead and selling — so the
 * department tree is a real roll-up, not a relabelling.
 *
 * Two sets of figures side by side, because either alone misleads. R&D at the
 * time of writing consumed 8.7% of its year, which reads as thrift until the
 * period columns show it spent 9.6% of what the year-to-date plan called for.
 *
 * The unallocated row is not a footnote: payroll, depreciation and shut-down
 * loss are tracked by category and never reach a cost centre, and they are
 * currently LARGER than everything that does. A summary that omitted them would
 * show a third of the company's spend and look complete.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Download, Loader2 } from 'lucide-react'
import { financeApi, financeDownload } from '@/lib/api'
import { cn } from '@/lib/utils'
import { UnallocatedLinesModal } from './UnallocatedLinesModal'
import { JvDetailModal } from './JvDetailModal'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

interface Metrics {
  plan_period: string; actual_period: string
  variance_period: string; variance_period_pct: string | null
  plan_full_year: string; actual_ytd: string
  remaining_full_year: string; consumed_pct: string | null
}
interface Leaf extends Metrics {
  cost_center_id: string; cost_center_code: string; cost_center_name: string
  department_code: string | null; department_name: string | null
  expense_centre: string
}
interface Node extends Metrics { code: string; label: string; children: Leaf[] }
interface Rollup {
  fiscal_year: number; month_from: number; month_to: number
  company: Metrics
  by_expense_centre: Node[]
  by_department: Node[]
  unallocated: {
    actual_period: string; actual_ytd: string
    breakdown: { key: string; label: string; actual_period: string; actual_ytd: string }[]
  }
  reconciliation: {
    placed_actual_period: string; unallocated_actual_period: string
    total_actual_period: string
  }
}

function money(v: string | null) {
  if (v === null) return '—'
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'
}
function pct(v: string | null) {
  // null means the denominator was zero. "0%" would read as "on budget" for a
  // cost centre spending against no budget at all.
  return v === null ? '—' : `${v}%`
}

export type Preset = 'month' | 'quarter' | 'ytd' | 'custom'
export interface Scope { kind: 'company' | 'centre' | 'department' | 'cost_centre'; key: string; label: string }

function presetRange(preset: Preset, now: number): [number, number] {
  if (preset === 'month') return [now, now]
  if (preset === 'quarter') {
    const q = Math.floor((now - 1) / 3)
    return [q * 3 + 1, Math.min(q * 3 + 3, now)]
  }
  return [1, now]
}

const selectCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

export function BudgetActualRollup({ fiscalYear, onFiscalYearChange, window: win,
                                    onWindowChange, scope, onScopeChange }: {
  fiscalYear: number
  onFiscalYearChange: (y: number) => void
  window: [number, number]
  onWindowChange: (w: [number, number], label: string) => void
  scope: Scope | null
  onScopeChange: (s: Scope | null) => void
}) {
  const thisYear = new Date().getFullYear()
  const currentMonth = fiscalYear === thisYear ? new Date().getMonth() + 1 : 12
  const [preset, setPreset] = useState<Preset>('ytd')
  const [custom, setCustom] = useState<[number, number]>([1, currentMonth])
  const [tab, setTab] = useState<'centre' | 'department'>('centre')
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [showUnallocated, setShowUnallocated] = useState(false)
  // Drill into one bucket of the unallocated row — most usefully "__unplaced__",
  // where every line is money nobody decided where to put.
  const [drillBucket, setDrillBucket] = useState<{ key: string; label: string } | null>(null)
  const [jvId, setJvId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const [from, to] = win

  const applyWindow = (next: [number, number]) => {
    const label = next[0] === next[1] ? MONTHS[next[0] - 1]
      : `${MONTHS[next[0] - 1]}–${MONTHS[next[1] - 1]}`
    onWindowChange(next, label)
  }
  const choosePreset = (p: Preset) => {
    setPreset(p)
    applyWindow(p === 'custom' ? custom : presetRange(p, currentMonth))
  }

  const { data, isFetching } = useQuery({
    queryKey: ['budget-actual-rollup', fiscalYear, from, to],
    queryFn: () => financeApi.get<Rollup>(
      `/gl/budget-actual/rollup?fiscal_year=${fiscalYear}&month_from=${from}&month_to=${to}`),
  })

  const nodes = useMemo(
    () => (tab === 'centre' ? data?.by_expense_centre : data?.by_department) ?? [],
    [data, tab])

  const toggle = (code: string) =>
    setOpen((s) => {
      const next = new Set(s)
      next.has(code) ? next.delete(code) : next.add(code)
      return next
    })

  const years = Array.from({ length: 5 }, (_, i) => thisYear - 2 + i)
  const windowLabel = from === to ? MONTHS[from - 1] : `${MONTHS[from - 1]}–${MONTHS[to - 1]}`

  return (
    <div className="mb-6 rounded-lg border border-neutral-200 bg-white">
      {/* controls */}
      <div className="flex flex-wrap items-center gap-2 border-b border-neutral-100 px-3 py-2.5">
        <select value={fiscalYear} onChange={(e) => onFiscalYearChange(Number(e.target.value))}
                className={selectCls}>
          {years.map((y) => <option key={y} value={y}>FY {y}</option>)}
        </select>
        <div className="flex rounded-lg border border-neutral-300 p-0.5">
          {([['month', 'Month'], ['quarter', 'Quarter'], ['ytd', 'YTD'],
             ['custom', 'Custom']] as [Preset, string][]).map(([p, label]) => (
            <button key={p} onClick={() => choosePreset(p)}
                    className={cn('rounded-md px-2.5 py-1 text-xs font-medium',
                      'cursor-pointer transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600',
                      preset === p ? 'bg-primary-600 text-white shadow-sm' : 'text-neutral-600 hover:bg-neutral-100')}>
              {label}
            </button>
          ))}
        </div>
        {preset === 'custom' && (
          <span className="flex items-center gap-1">
            <select value={custom[0]} className={selectCls}
                    onChange={(e) => {
                      const next: [number, number] = [Number(e.target.value), custom[1]]
                      setCustom(next); applyWindow(next)
                    }}>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
            <span className="text-xs text-neutral-400">to</span>
            <select value={custom[1]} className={selectCls}
                    onChange={(e) => {
                      const next: [number, number] = [custom[0], Number(e.target.value)]
                      setCustom(next); applyWindow(next)
                    }}>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
          </span>
        )}
        <span className="ml-auto text-xs text-neutral-400">
          {isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : `${windowLabel} ${fiscalYear}`}
        </span>
      </div>

      {!data ? (
        <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
      ) : (
        <>
          {/* company — clicking the header resets the composition to everything */}
          <button onClick={() => onScopeChange({ kind: 'company', key: '', label: 'All cost centres' })}
                  className={cn('w-full cursor-pointer border-b border-neutral-100 px-4 py-2 text-left text-xs transition-colors',
                    scope?.kind === 'company' ? 'bg-primary-50/70 font-medium text-primary-800'
                                              : 'text-neutral-500 hover:bg-neutral-50')}>
            Whole company — click for its composition
          </button>
          <div className="grid grid-cols-2 gap-px bg-neutral-100 lg:grid-cols-4">
            {[
              { label: `Plan ${windowLabel}`, v: data.company.plan_period },
              { label: `Actual ${windowLabel}`, v: data.company.actual_period },
              { label: 'Variance', v: data.company.variance_period,
                sub: pct(data.company.variance_period_pct),
                bad: Number(data.company.variance_period) < 0 },
              { label: 'Full-year plan', v: data.company.plan_full_year,
                sub: `${pct(data.company.consumed_pct)} consumed` },
            ].map((c) => (
              <div key={c.label} className="bg-white px-4 py-3.5">
                <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-500">{c.label}</p>
                <p className={cn('mt-1 font-mono text-xl font-semibold tabular-nums tracking-tight',
                  c.bad ? 'text-danger-600' : 'text-neutral-900')}>{money(c.v)}</p>
                {c.sub && <p className="mt-0.5 text-xs text-neutral-400">{c.sub}</p>}
              </div>
            ))}
          </div>

          {/* tree */}
          <div className="flex items-center gap-1 border-t border-neutral-100 px-3 py-2">
            {([['centre', 'By expense centre'], ['department', 'By department']] as const)
              .map(([t, label]) => (
                <button key={t} onClick={() => { setTab(t); setOpen(new Set()) }}
                        className={cn('rounded-md px-2.5 py-1 text-xs font-medium',
                          'cursor-pointer transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600',
                          tab === t ? 'bg-neutral-900 text-white' : 'text-neutral-500 hover:bg-neutral-100')}>
                  {label}
                </button>
              ))}
            <span className="ml-2 text-xs text-neutral-400">
              Same cost centres, two ways in
            </span>

            {/* Exports whichever grouping is on screen, fully expanded — a
                collapsed row is a convenience on a page and missing data in a
                spreadsheet. */}
            <button
              onClick={async () => {
                setExporting(true); setExportError(null)
                try {
                  await financeDownload(
                    `/gl/budget-actual/rollup/export?fiscal_year=${fiscalYear}` +
                    `&month_from=${from}&month_to=${to}&group_by=${tab}`,
                    `budget-actual-${tab}-FY${fiscalYear}.xlsx`)
                } catch (e) {
                  setExportError(e instanceof Error ? e.message : 'Export failed')
                } finally { setExporting(false) }
              }}
              disabled={exporting || !data}
              className={cn(
                'ml-auto inline-flex cursor-pointer items-center gap-1.5 rounded-lg border',
                'border-neutral-300 px-2.5 py-1.5 text-xs font-medium text-neutral-700',
                'transition-colors hover:bg-neutral-50',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600',
                'disabled:cursor-not-allowed disabled:opacity-50')}>
              {exporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                         : <Download className="h-3.5 w-3.5" />}
              Export to Excel
            </button>
          </div>
          {exportError && (
            <p role="alert" className="px-3 pb-2 text-xs text-danger-600">{exportError}</p>
          )}

          <div className="overflow-x-auto">
            <table className="w-full min-w-[60rem] text-sm">
              <thead className="sticky top-0 z-10">
                <tr className="border-y border-neutral-200 bg-neutral-50/95 text-xs font-medium text-neutral-600 backdrop-blur">
                  <th className="px-3 py-2 text-left font-medium">
                    {tab === 'centre' ? 'Expense centre' : 'Department'}
                  </th>
                  <th className="px-3 py-2 text-right font-medium">Plan {windowLabel}</th>
                  <th className="px-3 py-2 text-right font-medium">Actual {windowLabel}</th>
                  <th className="px-3 py-2 text-right font-medium">Variance</th>
                  <th className="px-3 py-2 text-right font-medium">%</th>
                  <th className="border-l border-neutral-200 px-3 py-2 text-right font-medium">Full-year plan</th>
                  <th className="px-3 py-2 text-right font-medium">Actual YTD</th>
                  <th className="px-3 py-2 text-right font-medium">Remaining</th>
                  <th className="px-3 py-2 text-right font-medium">Consumed</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((n) => (
                  <Row key={n.code || n.label} node={n} isOpen={open.has(n.code || n.label)}
                       onToggle={() => toggle(n.code || n.label)} tab={tab}
                       scope={scope} onScopeChange={onScopeChange} />
                ))}

                {/* Not a footnote — see the file header. */}
                <tr className="border-t-2 border-neutral-200 bg-amber-50/40">
                  <td className="px-3 py-2">
                    <button onClick={() => setShowUnallocated((v) => !v)}
                            className="flex cursor-pointer items-center gap-1 text-left text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                      <ChevronRight className={cn('h-3.5 w-3.5 motion-safe:transition-transform',
                        showUnallocated && 'rotate-90')} />
                      <span className="font-medium">Category-level (not allocated)</span>
                    </button>
                    <p className="pl-5 text-xs text-neutral-500">
                      Tracked by category, never spread over cost centres
                    </p>
                  </td>
                  <td className="px-3 py-2 text-right text-neutral-400">—</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.unallocated.actual_period)}</td>
                  <td colSpan={3} className="px-3 py-2 text-right text-neutral-400">—</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.unallocated.actual_ytd)}</td>
                  <td colSpan={2} className="px-3 py-2 text-right text-neutral-400">—</td>
                </tr>
                {showUnallocated && data.unallocated.breakdown.map((b) => (
                  <tr key={b.key} className="border-t border-neutral-100 bg-amber-50/30 text-neutral-600">
                    <td className="py-1.5 pl-10 pr-3 text-xs">
                      <button onClick={() => setDrillBucket({ key: b.key, label: b.label })}
                              className="cursor-pointer text-left hover:text-primary-700 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                        {b.label}
                      </button>
                      <button onClick={() => setDrillBucket({ key: b.key, label: b.label })}
                              className="ml-2 cursor-pointer rounded text-[11px] font-medium text-[#085E5E] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                        Vouchers
                      </button>
                    </td>
                    <td className="px-3 py-1.5 text-right text-neutral-300">—</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">{money(b.actual_period)}</td>
                    <td colSpan={3} className="px-3 py-1.5 text-right text-neutral-300">—</td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">{money(b.actual_ytd)}</td>
                    <td colSpan={2} className="px-3 py-1.5 text-right text-neutral-300">—</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                {/* The report's own proof: placed + unallocated == everything
                    posted in the window. A summary whose parts do not add to the
                    whole is what this page was rebuilt to stop being. */}
                <tr className="border-t-2 border-neutral-300 bg-neutral-50 font-semibold">
                  <td className="px-3 py-2">Total actual {windowLabel}</td>
                  {/* No plan figure beside this total on purpose: the plan
                      covers only what lands in a cost centre, while this actual
                      includes the category-level items, which are not budgeted
                      at all. Printing them side by side reads as a 12M overrun
                      that does not exist. The company plan is in the card above. */}
                  <td className="px-3 py-2 text-right text-neutral-300">—</td>
                  <td className="px-3 py-2 text-right font-mono">
                    {money(data.reconciliation.total_actual_period)}
                  </td>
                  <td colSpan={6} className="px-3 py-2 text-xs font-normal text-neutral-400">
                    = {money(data.reconciliation.placed_actual_period)} in cost centres
                    + {money(data.reconciliation.unallocated_actual_period)} category-level
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </>
      )}

      {drillBucket && (
        <UnallocatedLinesModal
          fiscalYear={fiscalYear} window={win} windowLabel={windowLabel}
          bucket={drillBucket.key} label={drillBucket.label}
          onClose={() => setDrillBucket(null)} onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={false} onClose={() => setJvId(null)} />
      )}
    </div>
  )
}

function Row({ node, isOpen, onToggle, tab, scope, onScopeChange }: {
  node: Node; isOpen: boolean; onToggle: () => void; tab: 'centre' | 'department'
  scope: Scope | null; onScopeChange: (s: Scope) => void
}) {
  const overspent = Number(node.variance_period) < 0
  const kind = tab === 'centre' ? 'centre' : 'department'
  const selected = scope?.kind === kind && scope.key === node.code
  return (
    <>
      <tr className={cn('border-t border-neutral-100 transition-colors hover:bg-neutral-50',
        selected && 'bg-primary-50/70 hover:bg-primary-50/70')}>
        <td className="px-3 py-2">
          <span className="flex items-center gap-1">
            {/* The chevron expands in place; the label drives the composition
                below. Two separate targets, because "show me the cost centres"
                and "show me what this is made of" are different questions. */}
            <button onClick={onToggle} aria-label="Expand rows"
                    className="shrink-0 cursor-pointer rounded p-0.5 hover:bg-neutral-200/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
              <ChevronRight className={cn('h-3.5 w-3.5 text-neutral-400 transition-transform',
                isOpen && 'rotate-90')} />
            </button>
            <button onClick={() => onScopeChange({ kind, key: node.code, label: node.label })}
                    className="cursor-pointer rounded text-left transition-colors hover:text-primary-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600">
              <span className="font-medium text-neutral-800">{node.label}</span>
              {node.code && <span className="ml-1.5 font-mono text-xs text-neutral-400">{node.code}</span>}
            </button>
          </span>
        </td>
        <Cells m={node} overspent={overspent} />
      </tr>
      {isOpen && node.children.map((c) => (
        <tr key={c.cost_center_id}
            className={cn('border-t border-neutral-100 bg-neutral-50/40 text-neutral-600',
              scope?.kind === 'cost_centre' && scope.key === c.cost_center_code && 'bg-primary-50/70')}>
          <td className="border-l-2 border-primary-200 py-1.5 pl-9 pr-3">
            <button className="cursor-pointer rounded text-left transition-colors hover:text-primary-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600"
                    onClick={() => onScopeChange({ kind: 'cost_centre', key: c.cost_center_code,
                                                   label: `${c.cost_center_code} ${c.cost_center_name}` })}>
              <span className="font-mono text-xs">{c.cost_center_code}</span>
              <span className="ml-1.5 text-xs">{c.cost_center_name}</span>
            </button>
            {/* In the department tree, say which expense centre a cost centre
                belongs to — that is exactly what the department spans. */}
            {tab === 'department' && (
              <span className="ml-1.5 rounded bg-neutral-200/70 px-1.5 py-0.5 text-[10px] text-neutral-600">
                {c.expense_centre}
              </span>
            )}
          </td>
          <Cells m={c} overspent={Number(c.variance_period) < 0} small />
        </tr>
      ))}
    </>
  )
}

function Cells({ m, overspent, small }: { m: Metrics; overspent: boolean; small?: boolean }) {
  // tabular-nums: proportional digits make a column of figures ripple; finance
  // scans these vertically for magnitude, not word by word.
  const cls = cn('px-3 text-right font-mono tabular-nums',
                 small ? 'py-1.5 text-xs' : 'py-2')
  return (
    <>
      <td className={cn(cls, 'text-neutral-500')}>{money(m.plan_period)}</td>
      <td className={cls}>{money(m.actual_period)}</td>
      <td className={cn(cls, overspent && 'text-danger-600 font-semibold')}>
        {money(m.variance_period)}
      </td>
      <td className={cn(cls, 'text-neutral-500')}>{pct(m.variance_period_pct)}</td>
      <td className={cn(cls, 'border-l border-neutral-200 text-neutral-500')}>
        {money(m.plan_full_year)}
      </td>
      <td className={cls}>{money(m.actual_ytd)}</td>
      <td className={cn(cls, Number(m.remaining_full_year) < 0 && 'text-danger-600')}>
        {money(m.remaining_full_year)}
      </td>
      <td className={cn(cls, 'text-neutral-500')}>{pct(m.consumed_pct)}</td>
    </>
  )
}
