/**
 * What a roll-up figure is made of — budget account × cost centre, over the
 * window and the selection made above.
 *
 * Deliberately the same query shape as the roll-up (crud/budget_rollup.py), so
 * these rows sum to the row that was clicked. Verified per level against
 * production: company, expense centre, department and cost centre each add up
 * exactly. A composition that sums to something else than the total above it
 * is worse than no composition at all.
 */
import { Fragment, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Loader2, Maximize2, Minimize2, X } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { Scope } from './BudgetActualRollup'

interface Metrics {
  plan_period: string; actual_period: string
  variance_period: string; variance_period_pct: string | null
  plan_full_year: string; actual_ytd: string
  remaining_full_year: string; consumed_pct: string | null
}
interface CcRow extends Metrics {
  cost_center_id: string; cost_center_code: string; cost_center_name: string
}
interface AcctRow extends Metrics {
  budget_account_id: string; code: string; name: string; children: CcRow[]
}
interface Resp { accounts: AcctRow[]; totals: Metrics }

function money(v: string) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'
}
const pct = (v: string | null) => (v === null ? '—' : `${v}%`)

export function BudgetActualBreakdown({ fiscalYear, window: win, windowLabel, scope,
                                        onClose }: {
  fiscalYear: number
  window: [number, number]
  windowLabel: string
  scope: Scope
  onClose: () => void
}) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [tall, setTall] = useState(false)
  const [from, to] = win
  const bodyRef = useRef<HTMLDivElement>(null)
  const [host, setHost] = useState<HTMLElement | null>(null)
  useEffect(() => { setHost(document.querySelector('main') ?? document.body) }, [])

  // Esc closes; a new selection scrolls the panel back to the top rather than
  // leaving the reader halfway down the previous scope's list.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  useEffect(() => { bodyRef.current?.scrollTo({ top: 0 }) }, [scope.kind, scope.key])

  const { data, isFetching } = useQuery({
    queryKey: ['budget-actual-breakdown', fiscalYear, from, to, scope.kind, scope.key],
    queryFn: () => financeApi.get<Resp>(
      `/gl/budget-actual/breakdown?fiscal_year=${fiscalYear}&month_from=${from}&month_to=${to}` +
      `&scope_kind=${scope.kind}&scope_key=${encodeURIComponent(scope.key)}`),
  })

  const rows = data?.accounts ?? []
  const toggle = (id: string) => setOpen((s) => {
    const next = new Set(s); next.has(id) ? next.delete(id) : next.add(id); return next
  })

  if (!host) return null
  return createPortal(
    <section
      role="dialog" aria-label={`Composition of ${scope.label}`}
      className={cn(
        'absolute inset-x-0 bottom-0 z-40 flex flex-col rounded-t-xl border-t border-neutral-200',
        'bg-white shadow-[0_-8px_32px_-12px_rgb(0_0_0/0.25)]',
        'motion-safe:animate-[slideUp_220ms_cubic-bezier(0.16,1,0.3,1)]',
        tall ? 'h-[85%]' : 'h-[58%]',
      )}
    >
      <style>{`@keyframes slideUp{from{transform:translateY(100%)}to{transform:translateY(0)}}`}</style>

      {/* A grab-handle shape, even though it is click-to-resize rather than
          drag — it reads as "this panel has a size" without a legend. */}
      <button onClick={() => setTall((v) => !v)}
              aria-label={tall ? 'Shrink panel' : 'Expand panel'}
              className="group mx-auto mt-2 mb-1 flex h-4 w-16 cursor-pointer items-center justify-center">
        <span className="h-1 w-10 rounded-full bg-neutral-300 transition-colors group-hover:bg-neutral-400" />
      </button>

      <header className="flex flex-wrap items-center gap-2 px-4 pb-2.5">
        <h2 className="text-sm font-semibold text-neutral-900">Composition</h2>
        <span className="rounded-md bg-primary-50 px-2 py-0.5 text-xs font-semibold text-primary-800 ring-1 ring-primary-100">
          {scope.label}
        </span>
        <span className="text-xs text-neutral-500">
          {windowLabel} {fiscalYear} · by budget account
        </span>
        <span className="ml-auto flex items-center gap-1 text-xs text-neutral-400">
          {isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          <button onClick={() => setTall((v) => !v)} aria-label={tall ? 'Shrink' : 'Expand'}
                  className="ml-1 cursor-pointer rounded-md p-1.5 text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
            {tall ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </button>
          <button onClick={onClose} aria-label="Close composition"
                  className="cursor-pointer rounded-md p-1.5 text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
            <X className="h-4 w-4" />
          </button>
        </span>
      </header>

      <div ref={bodyRef} className="flex-1 overflow-auto">
        <table className="w-full min-w-[56rem] text-sm">
          {/* Both rows live in <thead> so they stay pinned together — the
              total is the claim this panel makes (it equals the roll-up row
              that was clicked), so it has to stay on screen while the reader
              scrolls the rows that make it up. */}
          <thead className="sticky top-0 z-10">
            <tr className="border-b border-neutral-200 bg-neutral-50/95 text-xs font-medium text-neutral-600 backdrop-blur">
              <th className="px-3 py-2 text-left font-medium">Budget account</th>
              <th className="px-3 py-2 text-right font-medium">Plan {windowLabel}</th>
              <th className="px-3 py-2 text-right font-medium">Actual {windowLabel}</th>
              <th className="px-3 py-2 text-right font-medium">Variance</th>
              <th className="px-3 py-2 text-right font-medium">%</th>
              <th className="border-l border-neutral-200 px-3 py-2 text-right font-medium">Full-year plan</th>
              <th className="px-3 py-2 text-right font-medium">Actual YTD</th>
              <th className="px-3 py-2 text-right font-medium">Consumed</th>
            </tr>
            {data && (
              <tr className="border-b-2 border-neutral-300 bg-white/95 text-sm font-semibold text-neutral-900 backdrop-blur">
                <th className="px-3 py-2 text-left">
                  Total — {scope.label}
                  <span className="ml-2 text-xs font-normal text-neutral-400">
                    {rows.length} accounts
                  </span>
                </th>
                <TotalCell v={data.totals.plan_period} muted />
                <TotalCell v={data.totals.actual_period} />
                <TotalCell v={data.totals.variance_period}
                           bad={Number(data.totals.variance_period) < 0} />
                <TotalCell v={pct(data.totals.variance_period_pct)} raw muted />
                <TotalCell v={data.totals.plan_full_year} muted sep />
                <TotalCell v={data.totals.actual_ytd} />
                <TotalCell v={pct(data.totals.consumed_pct)} raw muted />
              </tr>
            )}
          </thead>
          <tbody>
            {!data && (
              <tr><td colSpan={8} className="px-3 py-8 text-center">
                <Loader2 className="mx-auto h-4 w-4 animate-spin text-neutral-400" /></td></tr>
            )}
            {data && rows.length === 0 && (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-xs text-neutral-400">
                Nothing budgeted or posted here in this window.
              </td></tr>
            )}
            {rows.map((a) => {
              const expandable = a.children.length > 1
              const isOpen = open.has(a.budget_account_id)
              return (
                <Fragment key={a.budget_account_id}>
                  <tr className="border-t border-neutral-100 transition-colors hover:bg-neutral-50">
                    <td className="px-3 py-2">
                      <span className="flex items-center gap-1">
                        {/* One cost centre under an account is the same row
                            twice — no expander for it. */}
                        {expandable ? (
                          <button onClick={() => toggle(a.budget_account_id)} aria-label="Expand cost centres"
                                  className="cursor-pointer rounded p-0.5 hover:bg-neutral-200/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                            <ChevronRight className={cn('h-3.5 w-3.5 text-neutral-400 motion-safe:transition-transform',
                              isOpen && 'rotate-90')} />
                          </button>
                        ) : <span className="w-3.5" />}
                        <span className="font-mono text-xs text-neutral-500">{a.code}</span>
                        <span className="text-neutral-800">{a.name}</span>
                        {expandable && (
                          <span className="text-xs text-neutral-400">({a.children.length})</span>
                        )}
                      </span>
                    </td>
                    <Cells m={a} />
                  </tr>
                  {isOpen && a.children.map((c) => (
                    <tr key={c.cost_center_id}
                        className="border-t border-neutral-100 bg-neutral-50/40 text-neutral-600">
                      <td className="border-l-2 border-primary-200 py-1.5 pl-9 pr-3">
                        <span className="font-mono text-xs">{c.cost_center_code}</span>
                        <span className="ml-1.5 text-xs">{c.cost_center_name}</span>
                      </td>
                      <Cells m={c} small />
                    </tr>
                  ))}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>,
    host,
  )
}

function TotalCell({ v, muted, bad, sep, raw }: {
  v: string; muted?: boolean; bad?: boolean; sep?: boolean; raw?: boolean
}) {
  return (
    <th className={cn('px-3 py-2 text-right font-mono tabular-nums font-semibold',
      sep && 'border-l border-neutral-200',
      bad ? 'text-danger-600' : muted ? 'text-neutral-500' : 'text-neutral-900')}>
      {raw ? v : money(v)}
    </th>
  )
}

function Cells({ m, small }: { m: Metrics; small?: boolean }) {
  const cls = cn('px-3 text-right font-mono tabular-nums',
                 small ? 'py-1.5 text-xs' : 'py-2')
  const over = Number(m.variance_period) < 0
  return (
    <>
      <td className={cn(cls, 'text-neutral-500')}>{money(m.plan_period)}</td>
      <td className={cls}>{money(m.actual_period)}</td>
      <td className={cn(cls, over && 'font-semibold text-danger-600')}>{money(m.variance_period)}</td>
      <td className={cn(cls, 'text-neutral-500')}>{pct(m.variance_period_pct)}</td>
      <td className={cn(cls, 'border-l border-neutral-200 text-neutral-500')}>{money(m.plan_full_year)}</td>
      <td className={cls}>{money(m.actual_ytd)}</td>
      <td className={cn(cls, 'text-neutral-500')}>{pct(m.consumed_pct)}</td>
    </>
  )
}
