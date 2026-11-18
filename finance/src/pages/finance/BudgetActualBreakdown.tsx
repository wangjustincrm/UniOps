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

import { TBody, THead, Table, Td, Th, Tr, Button, Badge } from '@uniops/shell'
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
                                        includeUnposted = false, onClose }: {
  fiscalYear: number
  window: [number, number]
  windowLabel: string
  scope: Scope
  includeUnposted?: boolean
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
    queryKey: ['budget-actual-breakdown', fiscalYear, from, to, scope.kind, scope.key, includeUnposted],
    queryFn: () => financeApi.get<Resp>(
      `/gl/budget-actual/breakdown?fiscal_year=${fiscalYear}&month_from=${from}&month_to=${to}` +
      `&scope_kind=${scope.kind}&scope_key=${encodeURIComponent(scope.key)}` +
      `&include_unposted=${includeUnposted}`),
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
        'absolute inset-x-0 bottom-0 z-40 flex flex-col rounded-t-xl border-t border-line',
        'bg-surface shadow-up',
        'motion-safe:animate-[slideUp_220ms_cubic-bezier(0.16,1,0.3,1)]',
        tall ? 'h-[85%]' : 'h-[58%]',
      )}
    >
      <style>{`@keyframes slideUp{from{transform:translateY(100%)}to{transform:translateY(0)}}`}</style>

      {/* A grab-handle shape, even though it is click-to-resize rather than
          drag — it reads as "this panel has a size" without a legend. */}
      <Button variant="subtle" size="icon-xs" onClick={() => setTall((v) => !v)}
              aria-label={tall ? 'Shrink panel' : 'Expand panel'}
              className="mx-auto mt-2 mb-1">
        <span className="h-1 w-10 rounded-full bg-line-strong transition-colors group-hover:bg-fg-faint" />
      </Button>

      <header className="flex flex-wrap items-center gap-2 px-4 pb-2.5">
        <h2 className="text-sm font-semibold text-fg">Composition</h2>
        <Badge variant="accent">
          {scope.label}
        </Badge>
        <span className="text-xs text-fg-muted">
          {windowLabel} {fiscalYear} · by budget account
        </span>
        <span className="ml-auto flex items-center gap-1 text-xs text-fg-muted">
          {isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          <Button variant="subtle" size="xs" onClick={() => setTall((v) => !v)} aria-label={tall ? 'Shrink' : 'Expand'}
                  className="ml-1">
            {tall ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </Button>
          <Button variant="subtle" size="icon-sm" onClick={onClose} aria-label="Close composition"
                 >
            <X className="h-4 w-4" />
          </Button>
        </span>
      </header>

      <div ref={bodyRef} className="flex-1 overflow-auto">
        <Table density="compact" className="min-w-[56rem]">
          {/* Both rows live in <thead> so they stay pinned together — the
              total is the claim this panel makes (it equals the roll-up row
              that was clicked), so it has to stay on screen while the reader
              scrolls the rows that make it up. */}
          <THead className="sticky top-0 z-10">
            <Tr className="bg-surface-subtle/95 text-xs font-medium text-fg-secondary backdrop-blur">
              <Th>Budget account</Th>
              <Th align="right">Plan {windowLabel}</Th>
              <Th align="right">Actual {windowLabel}</Th>
              <Th align="right">Variance</Th>
              <Th align="right">%</Th>
              <Th align="right" className="border-l border-line ">Full-year plan</Th>
              <Th align="right">Actual YTD</Th>
              <Th align="right">Consumed</Th>
            </Tr>
            {data && (
              <Tr className="border-b-2 bg-white/95 text-sm font-semibold text-fg backdrop-blur">
                <Th>
                  Total — {scope.label}
                  <span className="ml-2 text-xs font-normal text-fg-muted">
                    {rows.length} accounts
                  </span>
                </Th>
                <TotalCell v={data.totals.plan_period} muted />
                <TotalCell v={data.totals.actual_period} />
                <TotalCell v={data.totals.variance_period}
                           bad={Number(data.totals.variance_period) < 0} />
                <TotalCell v={pct(data.totals.variance_period_pct)} raw muted />
                <TotalCell v={data.totals.plan_full_year} muted sep />
                <TotalCell v={data.totals.actual_ytd} />
                <TotalCell v={pct(data.totals.consumed_pct)} raw muted />
              </Tr>
            )}
          </THead>
          <TBody>
            {!data && (
              <Tr><Td align="center" colSpan={8}>
                <Loader2 className="mx-auto h-4 w-4 animate-spin text-fg-muted" /></Td></Tr>
            )}
            {data && rows.length === 0 && (
              <Tr><Td align="center" colSpan={8} className="text-xs text-fg-muted">
                Nothing budgeted or posted here in this window.
              </Td></Tr>
            )}
            {rows.map((a) => {
              const expandable = a.children.length > 1
              const isOpen = open.has(a.budget_account_id)
              return (
                <Fragment key={a.budget_account_id}>
                  <Tr className="transition-colors hover:bg-surface-subtle">
                    <Td>
                      <span className="flex items-center gap-1">
                        {/* One cost centre under an account is the same row
                            twice — no expander for it. */}
                        {expandable ? (
                          <Button variant="subtle" size="icon-xs" onClick={() => toggle(a.budget_account_id)} aria-label="Expand cost centres"
                                 >
                            <ChevronRight className={cn('h-3.5 w-3.5 text-fg-muted motion-safe:transition-transform',
                              isOpen && 'rotate-90')} />
                          </Button>
                        ) : <span className="w-3.5" />}
                        <span className="font-mono text-xs text-fg-muted">{a.code}</span>
                        <span className="text-fg">{a.name}</span>
                        {expandable && (
                          <span className="text-xs text-fg-muted">({a.children.length})</span>
                        )}
                      </span>
                    </Td>
                    <Cells m={a} />
                  </Tr>
                  {isOpen && a.children.map((c) => (
                    <Tr key={c.cost_center_id}
                        className="bg-surface-subtle/40 text-fg-secondary">
                      <Td className="border-l-2 border-primary-200 pl-9 pr-3">
                        <span className="font-mono text-xs">{c.cost_center_code}</span>
                        <span className="ml-1.5 text-xs">{c.cost_center_name}</span>
                      </Td>
                      <Cells m={c} small />
                    </Tr>
                  ))}
                </Fragment>
              )
            })}
          </TBody>
        </Table>
      </div>
    </section>,
    host,
  )
}

function TotalCell({ v, muted, bad, sep, raw }: {
  v: string; muted?: boolean; bad?: boolean; sep?: boolean; raw?: boolean
}) {
  return (
    <Th className={cn('px-3 py-2 text-right font-mono tabular-nums font-semibold',
      sep && 'border-l border-line',
      bad ? 'text-danger-fg' : muted ? 'text-fg-muted' : 'text-fg')}>
      {raw ? v : money(v)}
    </Th>
  )
}

function Cells({ m, small }: { m: Metrics; small?: boolean }) {
  const cls = cn('px-3 text-right font-mono tabular-nums',
                 small ? 'py-1.5 text-xs' : 'py-2')
  const over = Number(m.variance_period) < 0
  return (
    <>
      <Td className={cn(cls, 'text-fg-muted')}>{money(m.plan_period)}</Td>
      <Td className={cls}>{money(m.actual_period)}</Td>
      <Td className={cn(cls, over && 'font-semibold text-danger-fg')}>{money(m.variance_period)}</Td>
      <Td className={cn(cls, 'text-fg-muted')}>{pct(m.variance_period_pct)}</Td>
      <Td className={cn(cls, 'border-l border-line text-fg-muted')}>{money(m.plan_full_year)}</Td>
      <Td className={cls}>{money(m.actual_ytd)}</Td>
      <Td className={cn(cls, 'text-fg-muted')}>{pct(m.consumed_pct)}</Td>
    </>
  )
}
