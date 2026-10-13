/**
 * AI Financial Analysis — Gross Margin (spec §6.5, §8 Overview).
 *
 * The conclusion first: which product-months need explaining and why (the
 * cost diagnosis behind each, down to its root), then the product × month
 * matrix as evidence. Margin is computed invoice by invoice: revenue is the
 * invoice's local amount excluding tax, cost of sales is NC's I5 for that
 * invoice plus any issue adjustment on it.
 */
import { Fragment, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import {
  AccessMessage, type Finding, FindingCard, num, qty, SyncBar, thisMonth, traceLink,
} from '@/lib/costAnalysis'

interface Cell {
  qty: number; revenue: number; cost: number; unit_price: number | null
  unit_cost: number | null; margin: number; margin_pct: number | null; invoices: string[]
}
interface Row {
  material_code: string; material_name: string | null; byproduct: string | null
  months: Record<string, Cell>
  total: { revenue: number; cost: number; margin: number; margin_pct: number | null }
}
interface MarginFinding extends Finding { material_code: string; material_name: string | null }
interface Resp {
  periods: string[]; rows: Row[]
  a7: { material_code: string; invoice: string; side: string }[]
  pending_invoices: { invoice: string; date: string | null; material_code: string; status_flag: number | null; qty: number | null; amount: number | null }[]
  costed_in_a_later_month: { material_code: string; invoice: string; invoice_month: string; cost_period: string }[]
  summary: { headline: string; findings: MarginFinding[] }
}

function pctCls(v: number | null | undefined, flagged: boolean) {
  if (v === null || v === undefined) return 'text-neutral-300'
  if (v < 0) return 'bg-red-50 font-semibold text-red-700'
  if (flagged) return 'bg-amber-50 text-amber-900'
  return 'text-neutral-800'
}

export default function GrossMarginPage() {
  const user = useAuthStore((s) => s.user)
  const [from, setFrom] = useState(`${thisMonth().slice(0, 4)}-01`)
  const [to, setTo] = useState(thisMonth())
  const [open, setOpen] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)

  const { data, error, isFetching } = useQuery({
    queryKey: ['cost-margin', from, to],
    queryFn: () => financeApi.get<Resp>(`/cost-analysis/margin?from=${from}&to=${to}`),
    enabled: from <= to,
  })
  if (!user) return <Navigate to="/login" replace />

  const flagged = new Set((data?.summary.findings ?? []).map((f) => `${f.material_code}|${f.period}`))
  const findings = data?.summary.findings ?? []
  const shown = showAll ? findings : findings.slice(0, 6)

  return (
    <PortalChromeLayout activeKey="portal:/finance/analysis/margin" title="Gross Margin"
                        subtitle="AI Financial Analysis · invoice by invoice, from NC's sales and cost of sales">
      <div className="mx-auto max-w-7xl">
        <SyncBar />
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
          <label className="text-xs text-neutral-500">From</label>
          <input type="month" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1" />
          <label className="text-xs text-neutral-500">To</label>
          <input type="month" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1" />
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />}
        </div>

        {error ? <AccessMessage error={error} /> : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /><p className="mt-2 text-xs text-neutral-500">Analysing every product-month…</p></div>
        ) : (
          <>
            <div className="mb-6 rounded-lg border border-[#085E5E]/30 bg-primary-50/40 p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-[#085E5E]">Summary</p>
              <p className="mt-1 text-base font-semibold leading-snug text-neutral-900">{data.summary.headline}</p>
              <div className="mt-3 space-y-2">
                {shown.map((f, i) => <FindingCard key={i} f={f} prefix={`${f.material_code} ${f.period} ·`} />)}
              </div>
              {findings.length > 6 && (
                <button onClick={() => setShowAll(!showAll)} className="mt-2 text-xs text-[#085E5E] underline">
                  {showAll ? 'Show the first 6' : `Show all ${findings.length}`}
                </button>
              )}
            </div>

            <h2 className="mb-3 border-b border-neutral-200 pb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">Supporting data</h2>
            <p className="mb-2 text-xs text-neutral-500">Gross margin % by product and month. Red = negative; amber = explained above. Click a cell for its figures.</p>
            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full text-xs">
                <thead className="bg-neutral-50 text-neutral-500">
                  <tr>
                    <th className="px-2 py-1.5 text-left font-medium">Product</th>
                    {data.periods.map((p) => <th key={p} className="px-2 py-1.5 text-right font-mono font-medium">{p.slice(2)}</th>)}
                    <th className="px-2 py-1.5 text-right font-medium">Revenue</th>
                    <th className="px-2 py-1.5 text-right font-medium">Margin</th>
                    <th className="px-2 py-1.5 text-right font-medium">%</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <Fragment key={r.material_code}>
                      <tr className="border-t border-neutral-100">
                        <td className="px-2 py-1.5">
                          <Link to={traceLink(r.material_code, from, to)} className="font-mono text-[#085E5E] underline">{r.material_code}</Link>
                          <span className="ml-1 text-neutral-500">{r.material_name}</span>
                          {r.byproduct && <span className="ml-1 rounded border border-neutral-200 px-1 text-[10px] text-neutral-500">by-product · {r.byproduct}</span>}
                        </td>
                        {data.periods.map((p) => {
                          const c = r.months[p]
                          const k = `${r.material_code}|${p}`
                          return (
                            <td key={p} onClick={() => c && setOpen(open === k ? null : k)}
                                title={c ? `Revenue ${num(c.revenue)} · cost ${num(c.cost)} · ${qty(c.qty)} sold` : ''}
                                className={cn('cursor-pointer px-2 py-1.5 text-right font-mono tabular-nums',
                                  pctCls(c?.margin_pct, flagged.has(k)), open === k && 'ring-2 ring-[#085E5E] ring-inset')}>
                              {c ? (c.margin_pct === null ? '—' : `${c.margin_pct}`) : ''}
                            </td>
                          )
                        })}
                        <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(r.total.revenue, 0)}</td>
                        <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', r.total.margin < 0 && 'text-red-700')}>{num(r.total.margin, 0)}</td>
                        <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (r.total.margin_pct ?? 0) < 0 && 'text-red-700')}>{r.total.margin_pct ?? '—'}</td>
                      </tr>
                      {data.periods.map((p) => {
                        const c = r.months[p]
                        if (!c || open !== `${r.material_code}|${p}`) return null
                        return (
                          <tr key={`${p}-d`} className="bg-neutral-50/70">
                            <td colSpan={data.periods.length + 4} className="px-3 py-2">
                              <div className="flex flex-wrap gap-x-6 gap-y-1">
                                <span><span className="text-neutral-500">{r.material_code} {p}:</span> {qty(c.qty)} sold</span>
                                <span>Revenue <b className="font-mono">{num(c.revenue)}</b> ({num(c.unit_price, 4)}/unit)</span>
                                <span>Cost of sales <b className="font-mono">{num(c.cost)}</b> ({num(c.unit_cost, 4)}/unit)</span>
                                <span className={cn(c.margin < 0 && 'text-red-700')}>Margin <b className="font-mono">{num(c.margin)}</b> ({c.margin_pct ?? '—'}%)</span>
                                <Link to={traceLink(r.material_code, from, p)} className="text-[#085E5E] underline">Trace</Link>
                                <Link to={`/finance/analysis/chain/${encodeURIComponent(r.material_code)}?period=${p}`} className="text-[#085E5E] underline">Cost chain</Link>
                                <Link to={`/finance/analysis/batches/${encodeURIComponent(r.material_code)}?from=${from}&to=${p}`} className="text-[#085E5E] underline">Batches</Link>
                                <Link to={`/finance/analysis/standard/${encodeURIComponent(r.material_code)}?from=${from}&to=${to}`} className="text-[#085E5E] underline">Standard cost</Link>
                              </div>
                              <p className="mt-1 font-mono text-[11px] text-neutral-400">Invoices: {c.invoices.join(', ')}</p>
                            </td>
                          </tr>
                        )
                      })}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            {data.costed_in_a_later_month.length > 0 && (
              <p className="mt-3 text-xs text-neutral-500">
                Costed by NC in a later month, counted in the month of the sale: {data.costed_in_a_later_month.map((x) => `${x.material_code} ${x.invoice} (${x.invoice_month} → ${x.cost_period})`).join('; ')}.
              </p>
            )}
            {data.pending_invoices.length > 0 && (
              <div className="mt-4 rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-xs text-neutral-700">
                <p className="font-medium">Invoices not approved in NC — not counted as revenue</p>
                <ul className="mt-1 list-disc pl-5">{data.pending_invoices.map((x, i) => <li key={i}><span className="font-mono">{x.invoice} {x.material_code}</span> {x.date} · {num(x.amount)} (status {x.status_flag ?? '—'})</li>)}</ul>
              </div>
            )}
            {data.a7.length > 0 && (
              <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                <p className="font-medium">Sales without a matching cost of sales, or the reverse (A7)</p>
                <ul className="mt-1 list-disc pl-5">{data.a7.map((x, i) => <li key={i}><span className="font-mono">{x.material_code} {x.invoice}</span> — {x.side}</li>)}</ul>
              </div>
            )}
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}
