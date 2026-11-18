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
import { type ReactNode, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Download, Loader2 } from 'lucide-react'
import { financeApi, financeDownload } from '@/lib/api'
import { cn, currentMonthISO } from '@/lib/utils'
import { UnallocatedLinesModal } from './UnallocatedLinesModal'
import { JvDetailModal } from './JvDetailModal'

import { Button, Checkbox, MenuItem, Select, TBody, TFoot, THead, Table, Td, Th, Tr, Badge, Card } from '@uniops/shell'
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

interface MonthCell { month: number; plan?: string; actual: string }
interface Metrics {
  monthly?: MonthCell[]
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
    actual_period: string; actual_ytd: string; monthly?: MonthCell[]
    breakdown: { key: string; label: string; actual_period: string; actual_ytd: string
                 monthly?: MonthCell[] }[]
  }
  reconciliation: {
    placed_actual_period: string; unallocated_actual_period: string
    total_actual_period: string; total_monthly?: MonthCell[]
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


export function BudgetActualRollup({ fiscalYear, onFiscalYearChange, window: win,
                                    onWindowChange, scope, onScopeChange,
                                    includeUnposted, onIncludeUnpostedChange }: {
  fiscalYear: number
  onFiscalYearChange: (y: number) => void
  window: [number, number]
  onWindowChange: (w: [number, number], label: string) => void
  scope: Scope | null
  onScopeChange: (s: Scope | null) => void
  includeUnposted: boolean
  onIncludeUnpostedChange: (v: boolean) => void
}) {
  const [thisYear, thisMonthNo] = currentMonthISO().split('-').map(Number)   // the plant's month
  const currentMonth = fiscalYear === thisYear ? thisMonthNo : 12
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
  const [expandMonths, setExpandMonths] = useState(false)

  const [from, to] = win
  // A single month has nothing to expand; the box is hidden and ignored then,
  // but keeps its state so it comes back ticked on the next multi-month window.
  const multiMonth = from < to
  const months = multiMonth && expandMonths
    ? Array.from({ length: to - from + 1 }, (_, i) => from + i) : []

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
    queryKey: ['budget-actual-rollup', fiscalYear, from, to, includeUnposted],
    queryFn: () => financeApi.get<Rollup>(
      `/gl/budget-actual/rollup?fiscal_year=${fiscalYear}&month_from=${from}&month_to=${to}` +
      `&include_unposted=${includeUnposted}`),
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
    <Card inset className="mb-6">
      {/* controls */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line-subtle px-3 py-2.5">
        <Select value={fiscalYear} onChange={(e) => onFiscalYearChange(Number(e.target.value))}
               >
          {years.map((y) => <option key={y} value={y}>FY {y}</option>)}
        </Select>
        <div className="flex rounded-lg border border-line-strong p-0.5">
          {([['month', 'Month'], ['quarter', 'Quarter'], ['ytd', 'YTD'],
             ['custom', 'Custom']] as [Preset, string][]).map(([p, label]) => (
            <Button key={p} onClick={() => choosePreset(p)}
                    variant={preset === p ? 'primary' : 'subtle'} size="xs" aria-pressed={preset === p}>
              {label}
            </Button>
          ))}
        </div>
        {preset === 'custom' && (
          <span className="flex items-center gap-1">
            <Select value={custom[0]}
                    onChange={(e) => {
                      const next: [number, number] = [Number(e.target.value), custom[1]]
                      setCustom(next); applyWindow(next)
                    }}>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </Select>
            <span className="text-xs text-fg-muted">to</span>
            <Select value={custom[1]}
                    onChange={(e) => {
                      const next: [number, number] = [custom[0], Number(e.target.value)]
                      setCustom(next); applyWindow(next)
                    }}>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </Select>
          </span>
        )}
        {/* NC counts a voucher once it is tallied (记账); a month still being
            closed reads nearly empty without this. Same toggle as Account
            Balance's, same rule (finance-api fiscal.status_filter). */}
        <label className="ml-2 inline-flex cursor-pointer select-none items-center gap-1.5 text-xs text-fg-secondary"
               title="Also count vouchers entered in NC but not yet tallied (记账). Discarded or deleted vouchers never count.">
          <Checkbox checked={includeUnposted}
                 onChange={(e) => onIncludeUnpostedChange(e.target.checked)}
                 className="h-3.5 w-3.5" />
          Include unposted vouchers
        </label>
        <span className="ml-auto text-xs text-fg-muted">
          {isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : `${windowLabel} ${fiscalYear}`}
        </span>
      </div>
      {includeUnposted && (
        <p className="border-b border-warning-line bg-warning-soft px-4 py-1.5 text-xs text-warning-fg">
          Actual includes vouchers not yet tallied in NC — these figures can still change until the month is closed.
        </p>
      )}

      {!data ? (
        <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
      ) : (
        <>
          {/* company — clicking the header resets the composition to everything */}
          <MenuItem active={scope?.kind === 'company'} aria-pressed={scope?.kind === 'company'}
                  onClick={() => onScopeChange({ kind: 'company', key: '', label: 'All cost centres' })}
                  className={cn('border-b border-line-subtle px-4 py-2 text-xs',
                    scope?.kind === 'company' ? 'font-medium text-accent-fg' : 'text-fg-muted')}>
            Whole company — click for its composition
          </MenuItem>
          <div className="grid grid-cols-2 gap-px bg-surface-sunken lg:grid-cols-4">
            {[
              { label: `Plan ${windowLabel}`, v: data.company.plan_period },
              { label: `Actual ${windowLabel}`, v: data.company.actual_period },
              { label: 'Variance', v: data.company.variance_period,
                sub: pct(data.company.variance_period_pct),
                bad: Number(data.company.variance_period) < 0 },
              { label: 'Full-year plan', v: data.company.plan_full_year,
                sub: `${pct(data.company.consumed_pct)} consumed` },
            ].map((c) => (
              <div key={c.label} className="bg-surface px-4 py-3.5">
                <p className="text-2xs font-medium text-fg-muted">{c.label}</p>
                <p className={cn('mt-1 font-mono text-xl font-semibold tabular-nums tracking-tight',
                  c.bad ? 'text-danger-fg' : 'text-fg')}>{money(c.v)}</p>
                {c.sub && <p className="mt-0.5 text-xs text-fg-muted">{c.sub}</p>}
              </div>
            ))}
          </div>

          {/* tree */}
          <div className="flex items-center gap-1 border-t border-line-subtle px-3 py-2">
            {([['centre', 'By expense centre'], ['department', 'By department']] as const)
              .map(([t, label]) => (
                <Button key={t} onClick={() => { setTab(t); setOpen(new Set()) }}
                        variant={tab === t ? 'primary' : 'subtle'} size="xs" aria-pressed={tab === t}>
                  {label}
                </Button>
              ))}
            <span className="ml-2 text-xs text-fg-muted">
              Same cost centres, two ways in
            </span>

            {multiMonth && (
              <label className="ml-auto inline-flex cursor-pointer select-none items-center gap-1.5 text-xs text-fg-secondary">
                <Checkbox checked={expandMonths}
                       onChange={(e) => setExpandMonths(e.target.checked)}
                       className="h-3.5 w-3.5" />
                Expand by months
              </label>
            )}

            {/* Exports whichever grouping is on screen, fully expanded — a
                collapsed row is a convenience on a page and missing data in a
                spreadsheet. */}
            <Button variant="secondary" size="sm"
              onClick={async () => {
                setExporting(true); setExportError(null)
                try {
                  await financeDownload(
                    `/gl/budget-actual/rollup/export?fiscal_year=${fiscalYear}` +
                    `&month_from=${from}&month_to=${to}&group_by=${tab}` +
                    (months.length ? '&by_month=true' : '') +
                    (includeUnposted ? '&include_unposted=true' : ''),
                    `budget-actual-${tab}-FY${fiscalYear}.xlsx`)
                } catch (e) {
                  setExportError(e instanceof Error ? e.message : 'Export failed')
                } finally { setExporting(false) }
              }}
              disabled={exporting || !data}
              className={multiMonth ? 'ml-3' : 'ml-auto'}>
              {exporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                         : <Download className="h-3.5 w-3.5" />}
              Export to Excel
            </Button>
          </div>
          {exportError && (
            <p role="alert" className="px-3 pb-2 text-xs text-danger-fg">{exportError}</p>
          )}

          <div className="overflow-x-auto">
            <Table density="compact" className="min-w-[60rem]"
                   style={months.length ? { minWidth: `${60 + months.length * 11}rem` } : undefined}>
              <THead className="sticky top-0 z-10">
                <Tr className="border-y bg-surface-subtle/95 text-xs font-medium text-fg-secondary backdrop-blur">
                  <Th>
                    {tab === 'centre' ? 'Expense centre' : 'Department'}
                  </Th>
                  {months.map((m, i) => (
                    <MonthHeads key={m} month={m} first={i === 0} />
                  ))}
                  <Th className={cn('px-3 py-2 text-right font-medium',
                    months.length > 0 && 'border-l border-line')}>Plan {windowLabel}</Th>
                  <Th align="right">Actual {windowLabel}</Th>
                  <Th align="right">Variance</Th>
                  <Th align="right">%</Th>
                  <Th align="right" className="border-l border-line ">Full-year plan</Th>
                  <Th align="right">Actual YTD</Th>
                  <Th align="right">Remaining</Th>
                  <Th align="right">Consumed</Th>
                </Tr>
              </THead>
              <TBody>
                {nodes.map((n) => (
                  <Row key={n.code || n.label} node={n} isOpen={open.has(n.code || n.label)}
                       onToggle={() => toggle(n.code || n.label)} tab={tab} months={months}
                       scope={scope} onScopeChange={onScopeChange} />
                ))}

                {/* Not a footnote — see the file header. */}
                <Tr className="border-t-2 bg-warning-soft/40">
                  <Td>
                    <Button variant="subtle" size="xs" onClick={() => setShowUnallocated((v) => !v)}
                            className="text-left">
                      <ChevronRight className={cn('h-3.5 w-3.5 motion-safe:transition-transform',
                        showUnallocated && 'rotate-90')} />
                      <span className="font-medium">Category-level (not allocated)</span>
                    </Button>
                    <p className="pl-5 text-xs text-fg-muted">
                      Tracked by category, never spread over cost centres
                    </p>
                  </Td>
                  <MonthActualOnly months={months} cells={data.unallocated.monthly} />
                  <Td className={cn('px-3 py-2 text-right text-fg-muted',
                    months.length > 0 && 'border-l border-line')}>—</Td>
                  <Td align="right" className="font-mono">{money(data.unallocated.actual_period)}</Td>
                  <Td align="right" colSpan={3} className="text-fg-muted">—</Td>
                  <Td align="right" className="font-mono">{money(data.unallocated.actual_ytd)}</Td>
                  <Td align="right" colSpan={2} className="text-fg-muted">—</Td>
                </Tr>
                {showUnallocated && data.unallocated.breakdown.map((b) => (
                  <Tr key={b.key} className="bg-warning-soft/30 text-fg-secondary">
                    <Td className="pl-10 pr-3 text-xs">
                      <Button variant="link" onClick={() => setDrillBucket({ key: b.key, label: b.label })}
                              className="text-left">
                        {b.label}
                      </Button>
                      <Button variant="link" onClick={() => setDrillBucket({ key: b.key, label: b.label })}
                              className="ml-2 text-2xs">
                        Vouchers
                      </Button>
                    </Td>
                    <MonthActualOnly months={months} cells={b.monthly} small />
                    <Td className={cn('px-3 py-1.5 text-right text-fg-faint',
                      months.length > 0 && 'border-l border-line')}>—</Td>
                    <Td align="right" className="font-mono text-xs">{money(b.actual_period)}</Td>
                    <Td align="right" colSpan={3} className="text-fg-faint">—</Td>
                    <Td align="right" className="font-mono text-xs">{money(b.actual_ytd)}</Td>
                    <Td align="right" colSpan={2} className="text-fg-faint">—</Td>
                  </Tr>
                ))}
              </TBody>
              <TFoot>
                {/* The report's own proof: placed + unallocated == everything
                    posted in the window. A summary whose parts do not add to the
                    whole is what this page was rebuilt to stop being. */}
                <Tr className="border-t-2 bg-surface-subtle font-semibold">
                  <Td>Total actual {windowLabel}</Td>
                  <MonthActualOnly months={months} cells={data.reconciliation.total_monthly} />
                  {/* No plan figure beside this total on purpose: the plan
                      covers only what lands in a cost centre, while this actual
                      includes the category-level items, which are not budgeted
                      at all. Printing them side by side reads as a 12M overrun
                      that does not exist. The company plan is in the card above. */}
                  <Td className={cn('px-3 py-2 text-right text-fg-faint',
                    months.length > 0 && 'border-l border-line')}>—</Td>
                  <Td align="right" className="font-mono">
                    {money(data.reconciliation.total_actual_period)}
                  </Td>
                  <Td colSpan={6} className="text-xs font-normal text-fg-muted">
                    = {money(data.reconciliation.placed_actual_period)} in cost centres
                    + {money(data.reconciliation.unallocated_actual_period)} category-level
                  </Td>
                </Tr>
              </TFoot>
            </Table>
          </div>
        </>
      )}

      {drillBucket && (
        <UnallocatedLinesModal
          fiscalYear={fiscalYear} window={win} windowLabel={windowLabel}
          bucket={drillBucket.key} label={drillBucket.label}
          includeUnposted={includeUnposted}
          onClose={() => setDrillBucket(null)} onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={false} onClose={() => setJvId(null)} />
      )}
    </Card>
  )
}

function Row({ node, isOpen, onToggle, tab, months, scope, onScopeChange }: {
  node: Node; isOpen: boolean; onToggle: () => void; tab: 'centre' | 'department'
  months: number[]
  scope: Scope | null; onScopeChange: (s: Scope) => void
}) {
  const overspent = Number(node.variance_period) < 0
  const kind = tab === 'centre' ? 'centre' : 'department'
  const selected = scope?.kind === kind && scope.key === node.code
  return (
    <>
      <Tr className={cn('border-t border-line-subtle transition-colors hover:bg-surface-subtle',
        selected && 'bg-primary-50/70 hover:bg-primary-50/70')}>
        <Td>
          <span className="flex items-center gap-1">
            {/* The chevron expands in place; the label drives the composition
                below. Two separate targets, because "show me the cost centres"
                and "show me what this is made of" are different questions. */}
            <Button variant="subtle" size="icon-xs" onClick={onToggle} aria-label="Expand rows"
                    className="shrink-0">
              <ChevronRight className={cn('h-3.5 w-3.5 text-fg-muted transition-transform',
                isOpen && 'rotate-90')} />
            </Button>
            <Button variant="link" size="xs" onClick={() => onScopeChange({ kind, key: node.code, label: node.label })}
                    className="h-auto text-left text-sm text-fg hover:text-accent-fg">
              <span className="font-medium text-fg">{node.label}</span>
              {node.code && <span className="ml-1.5 font-mono text-xs text-fg-muted">{node.code}</span>}
            </Button>
          </span>
        </Td>
        <Cells m={node} overspent={overspent} months={months} />
      </Tr>
      {isOpen && node.children.map((c) => (
        <Tr key={c.cost_center_id}
            className={cn('border-t border-line-subtle bg-surface-subtle/40 text-fg-secondary',
              scope?.kind === 'cost_centre' && scope.key === c.cost_center_code && 'bg-primary-50/70')}>
          <Td className="border-l-2 border-primary-200 pl-9 pr-3">
            <Button variant="link" size="xs" className="h-auto text-left text-fg hover:text-accent-fg"
                    onClick={() => onScopeChange({ kind: 'cost_centre', key: c.cost_center_code,
                                                   label: `${c.cost_center_code} ${c.cost_center_name}` })}>
              <span className="font-mono text-xs">{c.cost_center_code}</span>
              <span className="ml-1.5 text-xs">{c.cost_center_name}</span>
            </Button>
            {/* In the department tree, say which expense centre a cost centre
                belongs to — that is exactly what the department spans. */}
            {tab === 'department' && (
              <Badge size="sm" className="ml-1.5">
                {c.expense_centre}
              </Badge>
            )}
          </Td>
          <Cells m={c} overspent={Number(c.variance_period) < 0} months={months} small />
        </Tr>
      ))}
    </>
  )
}

function MonthHeads({ month, first }: { month: number; first: boolean }) {
  return (
    <>
      <Th className={cn('whitespace-nowrap px-3 py-2 text-right font-medium text-fg-muted',
        !first && 'border-l border-line-subtle')}>{MONTHS[month - 1]} plan</Th>
      <Th align="right">{MONTHS[month - 1]} actual</Th>
    </>
  )
}

/** Month columns for a row that has no plan (category-level, totals): the plan
 *  cell stays "—", same as the window plan cell beside it. */
function MonthActualOnly({ months, cells, small }: {
  months: number[]; cells?: MonthCell[]; small?: boolean
}) {
  const by = new Map((cells ?? []).map((c) => [c.month, c]))
  const cls = cn('px-3 text-right font-mono tabular-nums', small ? 'py-1.5 text-xs' : 'py-2')
  return (
    <>
      {months.map((m, i) => (
        <MonthPair key={m} first={i === 0}
                   plan={<span className="text-fg-faint">—</span>}
                   actual={money(by.get(m)?.actual ?? null)} cls={cls} />
      ))}
    </>
  )
}

function MonthPair({ plan, actual, cls, first }: {
  plan: ReactNode; actual: ReactNode; cls: string; first: boolean
}) {
  return (
    <>
      <Td className={cn(cls, 'text-fg-muted', !first && 'border-l border-line-subtle')}>{plan}</Td>
      <Td className={cls}>{actual}</Td>
    </>
  )
}

function Cells({ m, overspent, months, small }: {
  m: Metrics; overspent: boolean; months: number[]; small?: boolean
}) {
  // tabular-nums: proportional digits make a column of figures ripple; finance
  // scans these vertically for magnitude, not word by word.
  const cls = cn('px-3 text-right font-mono tabular-nums',
                 small ? 'py-1.5 text-xs' : 'py-2')
  const by = new Map((m.monthly ?? []).map((c) => [c.month, c]))
  return (
    <>
      {/* Plan and actual per month only — no monthly variance (user, 2026-09-25). */}
      {months.map((mo, i) => (
        <MonthPair key={mo} first={i === 0} cls={cls}
                   plan={money(by.get(mo)?.plan ?? null)}
                   actual={money(by.get(mo)?.actual ?? null)} />
      ))}
      <Td className={cn(cls, 'text-fg-muted', months.length > 0 && 'border-l border-line')}>
        {money(m.plan_period)}
      </Td>
      <Td className={cls}>{money(m.actual_period)}</Td>
      <Td className={cn(cls, overspent && 'text-danger-fg font-semibold')}>
        {money(m.variance_period)}
      </Td>
      <Td className={cn(cls, 'text-fg-muted')}>{pct(m.variance_period_pct)}</Td>
      <Td className={cn(cls, 'border-l border-line text-fg-muted')}>
        {money(m.plan_full_year)}
      </Td>
      <Td className={cls}>{money(m.actual_ytd)}</Td>
      <Td className={cn(cls, Number(m.remaining_full_year) < 0 && 'text-danger-fg')}>
        {money(m.remaining_full_year)}
      </Td>
      <Td className={cn(cls, 'text-fg-muted')}>{pct(m.consumed_pct)}</Td>
    </>
  )
}
