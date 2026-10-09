/**
 * AI Financial Analysis — Anomalies.
 *
 * Every rule of spec §7 run over the NC cost mirror for a range of months.
 * Each finding names the documents behind it and, for stock left at a
 * month-end with a value that is wrong, where that stock went the month after
 * (production crosses month-ends: semi-finished product made at the end of a
 * month becomes finished goods the next). No notifications: this page is the
 * only place these show up.
 */
import { Fragment, useMemo, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { deriveTabMeta, useOptionalTabStoreApi } from '@uniops/shell'
import { financeRoutes } from '@/app/routes'
import { ChevronDown, ChevronRight, Loader2, Search } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import {
  AccessMessage, type Anomaly, type AnomaliesResp, type Diagnosis, DiagnosisPanel, num,
  OpenPeriodBadge, pct, qty, RULE_HELP, SeverityBadge, SyncBar, thisMonth,
} from '@/lib/costAnalysis'

const RULE_ORDER = ['A1', 'A3', 'A5', 'A8', 'A2', 'A4', 'A6']

function summary(a: Anomaly): string {
  const m = a.metrics
  switch (a.rule) {
    case 'A1': return `${qty(m.qty)} produced at ${num(m.unit, 4)} / unit (total ${num(m.amount)})`
    case 'A2': return `${num(m.previous_unit, 4)} (${m.previous_period}) → ${num(m.unit, 4)}, ${pct(m.change)}`
    case 'A3': return `${a.detail ?? ''}: ${qty(m.closing_qty)} on hand, value ${num(m.closing_amount)}`
    case 'A4': return `Variance ${num(m.variance)} vs consumption ${num(m.consumption)}${m.ratio != null ? ` (${pct(m.ratio)})` : ' (nothing consumed)'}`
    case 'A5': return `${String(m.family).replace(/_/g, ' ')} · ${a.detail ?? ''} · difference ${num(m.difference?.amount)}`
    case 'A6': {
      const landed = (m.landed_on ?? []).map((x: any) => x.material_code).join(', ')
      return `${num(m.amount)}${landed ? ` → ${landed}` : ''}`
    }
    case 'A8': return a.detail ?? ''
    default: return ''
  }
}

function traceHref(a: Anomaly, from: string, to: string): string | null {
  if (!a.material_code) return null
  const p = new URLSearchParams({ from: a.period && a.period < from ? a.period : from, to })
  return `/finance/analysis/trace/${encodeURIComponent(a.material_code)}?${p.toString()}`
}

function prevMonth(p: string): string {
  const y = Number(p.slice(0, 4)); const mo = Number(p.slice(5, 7))
  return mo === 1 ? `${y - 1}-12` : `${y}-${String(mo - 1).padStart(2, '0')}`
}

/** Why it happened — the diagnosis for this material around this month. */
function Why({ a }: { a: Anomaly }) {
  const enabled = !!a.material_code && !!a.period
  const from = a.period ? prevMonth(a.period) : ''
  const { data, error, isLoading } = useQuery({
    queryKey: ['cost-diagnosis', a.material_code, from, a.period],
    queryFn: () => financeApi.get<Diagnosis>(
      `/cost-analysis/diagnosis?material=${encodeURIComponent(a.material_code!)}&from=${from}&to=${a.period}`),
    enabled,
    staleTime: 60_000,
  })
  if (!enabled) return null
  if (isLoading) return <p className="flex items-center gap-1 text-xs text-neutral-500"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Analysing…</p>
  if (error || !data) return null
  return <DiagnosisPanel d={data} compact />
}

function Detail({ a }: { a: Anomaly }) {
  const m = a.metrics
  return (
    <div className="space-y-2 text-xs text-neutral-600">
      <Why a={a} />
      <p className="text-neutral-500">{RULE_HELP[a.rule]}</p>
      {a.rule === 'A5' && (
        <div className="grid gap-2 sm:grid-cols-2">
          {(['in', 'out'] as const).map((side) => (
            <div key={side} className="rounded border border-neutral-200 bg-white p-2">
              <p className="font-medium text-neutral-700">{side === 'in' ? 'Receipt leg' : 'Issue leg'}</p>
              <p>Qty {qty(m[side]?.qty)} · Amount {num(m[side]?.amount)}</p>
              <p className="font-mono">{(m[side]?.bills ?? []).join(', ') || 'none'}</p>
            </div>
          ))}
          <p className="sm:col-span-2">
            Transfer document: <span className="font-mono">{m.upstream_code ?? 'none in NC'}</span>
            {m.materials?.length > 1 && <> · materials {m.materials.join(', ')}</>}
          </p>
        </div>
      )}
      {a.rule === 'A8' && m.computed && (
        <p>Ledger roll closes at {qty(m.computed.qty)} / {num(m.computed.amount)}; NC balance{' '}
          {m.nc_balance ? `${qty(m.nc_balance.qty)} / ${num(m.nc_balance.amount)}` : 'none'}.</p>
      )}
      {a.rule === 'A8' && m.diffs && (
        <pre className="max-h-48 overflow-auto rounded bg-white p-2 text-[11px]">{JSON.stringify(m.diffs, null, 2)}</pre>
      )}
      {a.next_month && (
        <div>
          <p className="font-medium text-neutral-700">Where this stock went the following month</p>
          {a.next_month.length === 0 ? (
            <p className="text-neutral-500">Not consumed yet.</p>
          ) : (
            <table className="mt-1 w-full max-w-2xl text-xs">
              <thead><tr className="text-left text-neutral-400">
                <th className="py-1 pr-3 font-medium">Production order</th>
                <th className="py-1 pr-3 font-medium">Made</th>
                <th className="py-1 pr-3 text-right font-medium">Qty</th>
                <th className="py-1 pr-3 text-right font-medium">Value</th>
                <th className="py-1 text-right font-medium">Unit</th>
              </tr></thead>
              <tbody>
                {a.next_month.map((f, i) => (
                  <tr key={i} className="border-t border-neutral-100">
                    <td className="py-1 pr-3 font-mono">{f.mo_code ?? '—'}</td>
                    <td className="py-1 pr-3">{f.products.map((p) => `${p.material_code} ${p.material_name ?? ''}`).join('; ') || '—'}</td>
                    <td className="py-1 pr-3 text-right font-mono tabular-nums">{qty(f.qty)}</td>
                    <td className={cn('py-1 pr-3 text-right font-mono tabular-nums', (f.amount ?? 0) < 0 && 'text-red-700')}>{num(f.amount)}</td>
                    <td className="py-1 text-right font-mono tabular-nums">{num(f.unit, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {a.refs.length > 0 && (
        <p>NC documents: <span className="font-mono">{a.refs.join(', ')}</span></p>
      )}
    </div>
  )
}

export default function CostAnomaliesPage() {
  const user = useAuthStore((s) => s.user)
  const [from, setFrom] = useState(`${thisMonth().slice(0, 4)}-01`)
  const [to, setTo] = useState(thisMonth())
  const [rule, setRule] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  // Optional: outside the tab shell (iframe embed) there is no store, and the
  // plain navigation is all that is needed.
  const tabs = useOptionalTabStoreApi()

  /** A material's trace tab may already be open on an older range; the shell
   *  only focuses an existing tab, so point it at this link first. */
  const retarget = (href: string) => {
    if (!tabs) return
    const q = href.indexOf('?')
    const meta = deriveTabMeta(financeRoutes, href.slice(0, q), href.slice(q))
    if (meta && tabs.getState().tabs.some((t) => t.key === meta.key)) {
      tabs.getState().setTabPath(meta.key, meta.path!)
    }
  }

  const { data, error, isFetching } = useQuery({
    queryKey: ['cost-anomalies', from, to],
    queryFn: () => financeApi.get<AnomaliesResp>(`/cost-analysis/anomalies?from=${from}&to=${to}`),
    enabled: !!from && !!to && from <= to,
  })

  const rows = useMemo(() => {
    const s = search.trim().toLowerCase()
    return (data?.anomalies ?? []).filter((a) =>
      (!rule || a.rule === rule)
      && (!s || `${a.material_code ?? ''} ${a.material_name ?? ''} ${a.refs.join(' ')}`.toLowerCase().includes(s)))
  }, [data, rule, search])

  if (!user) return <Navigate to="/login" replace />

  const toggle = (i: number) => setExpanded((prev) => {
    const next = new Set(prev)
    if (next.has(i)) next.delete(i); else next.add(i)
    return next
  })

  return (
    <PortalChromeLayout activeKey="portal:/finance/analysis/anomalies" title="Cost Anomalies"
                        subtitle="AI Financial Analysis · computed from NC's inventory and cost data">
      <div className="mx-auto max-w-7xl">
        <SyncBar />

        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
          <label className="text-xs text-neutral-500">From</label>
          <input type="month" value={from} onChange={(e) => setFrom(e.target.value)}
                 className="rounded-lg border border-neutral-300 px-2 py-1" />
          <label className="text-xs text-neutral-500">To</label>
          <input type="month" value={to} onChange={(e) => setTo(e.target.value)}
                 className="rounded-lg border border-neutral-300 px-2 py-1" />
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-neutral-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="Material or NC document"
                   className="w-64 rounded-lg border border-neutral-300 py-1 pl-7 pr-2" />
          </div>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />}
          {data && data.open_periods.length > 0 && (
            <span className="ml-auto text-xs text-neutral-500">
              Not closed in NC yet: {data.open_periods.join(', ')} — figures there can still change.
            </span>
          )}
        </div>

        {error ? <AccessMessage error={error} /> : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <>
            <div className="mb-4 grid gap-2 sm:grid-cols-4 lg:grid-cols-7">
              {RULE_ORDER.map((r) => {
                const info = data.rules[r]
                const n = data.counts[r] ?? 0
                return (
                  <button key={r} onClick={() => setRule(rule === r ? null : r)} title={RULE_HELP[r]}
                          className={cn('rounded-lg border p-2.5 text-left transition-colors',
                            rule === r ? 'border-[#085E5E] bg-primary-50/60' : 'border-neutral-200 hover:bg-neutral-50')}>
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs text-neutral-400">{r}</span>
                      {n > 0 && <SeverityBadge severity={info?.severity ?? 'medium'} />}
                    </div>
                    <p className="mt-1 text-xs font-medium leading-snug text-neutral-700">{info?.title}</p>
                    <p className="mt-1 font-mono text-lg font-semibold text-neutral-900">{n}</p>
                  </button>
                )
              })}
            </div>

            {rows.length === 0 ? (
              <div className="rounded-lg border border-neutral-200 px-4 py-10 text-center text-sm text-neutral-500">
                Nothing found for this range.
              </div>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-neutral-200">
                <table className="w-full text-sm">
                  <thead className="bg-neutral-50">
                    <tr className="text-left text-xs text-neutral-500">
                      <th className="w-6 px-2 py-2" />
                      <th className="px-2.5 py-2 font-medium">Severity</th>
                      <th className="px-2.5 py-2 font-medium">Rule</th>
                      <th className="px-2.5 py-2 font-medium">Month</th>
                      <th className="px-2.5 py-2 font-medium">Material</th>
                      <th className="px-2.5 py-2 font-medium">What happened</th>
                      <th className="px-2.5 py-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((a, i) => {
                      const open = expanded.has(i)
                      const href = traceHref(a, from, to)
                      return (
                        <Fragment key={i}>
                          <tr className="cursor-pointer border-t border-neutral-100 hover:bg-neutral-50" onClick={() => toggle(i)}>
                            <td className="px-2 py-2 text-neutral-400">
                              {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                            </td>
                            <td className="px-2.5 py-2"><SeverityBadge severity={a.severity} /></td>
                            <td className="px-2.5 py-2 text-xs"><span className="font-mono text-neutral-400">{a.rule}</span> {a.title}</td>
                            <td className="whitespace-nowrap px-2.5 py-2 font-mono text-xs">
                              {a.period ?? '—'}{a.period_closed === false && <OpenPeriodBadge />}
                            </td>
                            <td className="px-2.5 py-2">
                              {a.material_code && <span className="font-mono text-xs">{a.material_code}</span>}
                              <span className="ml-1 text-xs text-neutral-500">{a.material_name}</span>
                            </td>
                            <td className="px-2.5 py-2 text-xs">{summary(a)}</td>
                            <td className="px-2.5 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                              {href && (
                                <Link to={href} onClick={() => retarget(href)}
                                      className="whitespace-nowrap text-xs text-[#085E5E] underline">
                                  Trace
                                </Link>
                              )}
                            </td>
                          </tr>
                          {open && (
                            <tr className="bg-neutral-50/60">
                              <td />
                              <td colSpan={6} className="px-2.5 py-3"><Detail a={a} /></td>
                            </tr>
                          )}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}
