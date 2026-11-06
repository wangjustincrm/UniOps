/**
 * AI Financial Analysis — Standard Cost (spec §6.7). A SUGGESTION: nothing is
 * written, NC keeps costing actuals.
 *
 * The conclusion first (the suggested standard, and which months standard
 * costing would have changed and why), then the card rolled up level by level,
 * the BOM revisions it implies, and 2026 replayed against it. Conversion is
 * suggested on NC's own per-unit basis; the page says plainly that the basis
 * is Finance's decision.
 */
import { Fragment, useEffect, useState } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Download, Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccessMessage, num, SyncBar, thisMonth, traceLink } from '@/lib/costAnalysis'

interface Line {
  material_code: string; material_name: string | null; std_usage: number; usage_samples: number
  outliers_dropped: number; usage_min: number | null; usage_max: number | null
  bom_qty_per: number | null; bom_uom: string | null; bom_deviation?: number
  self_made: boolean; std_price: number | null; price_basis: string; std_cost: number; child: Card | null
}
interface Card {
  material_code: string; material_name: string | null; usage_basis: string; samples: number
  lines: Line[]; conversion: { unit: number; months: string[]; excluded_stoppage: number }
  materials_unit: number; std_unit: number
}
interface SimRow {
  period: string; qty: number; actual: number; actual_unit: number | null; standard: number; std_unit: number
  usage_variance: number; price_variance: number; input_cost_variance: number
  conversion_variance: number; adjustments: number; unexplained: number
}
interface Resp {
  params: Record<string, number | boolean>; card: Card; simulation: SimRow[]
  summary: { headline: string; because: string[]
             bom_suggestions: { material_code: string; bom: number | null; actual_median: number; deviation: number | null; uom: string | null; kind: string }[] }
}

function CardRows({ c, depth }: { c: Card; depth: number }) {
  return (
    <>
      {c.lines.map((l) => <LineRow key={`${c.material_code}-${l.material_code}`} l={l} depth={depth} />)}
      <tr className="border-t border-neutral-100 bg-neutral-50/60">
        <td className="py-1.5 pr-2 text-neutral-500" style={{ paddingLeft: 26 + depth * 18 }}>Conversion (per unit, NC basis{c.conversion.excluded_stoppage ? ', stoppage left out' : ''})</td>
        <td colSpan={5} />
        <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(c.conversion.unit, 4)}</td>
      </tr>
      <tr className="border-t border-neutral-200">
        <td className="py-1.5 pr-2 font-semibold" style={{ paddingLeft: 26 + depth * 18 }}>Standard {c.material_code}</td>
        <td colSpan={5} className="text-[11px] text-neutral-500">usage from {c.samples} {c.usage_basis}</td>
        <td className="px-2 py-1.5 text-right font-mono font-semibold tabular-nums">{num(c.std_unit, 4)}</td>
      </tr>
    </>
  )
}

function LineRow({ l, depth }: { l: Line; depth: number }) {
  const [open, setOpen] = useState(false)
  const dev = l.bom_deviation
  return (
    <>
      <tr className="border-t border-neutral-100">
        <td className="py-1.5 pr-2" style={{ paddingLeft: 8 + depth * 18 }}>
          <span className="inline-flex items-center gap-1">
            {l.child ? (
              <button onClick={() => setOpen(!open)} className="text-neutral-400">
                {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              </button>
            ) : <span className="inline-block w-3.5" />}
            <Link to={traceLink(l.material_code, `${thisMonth().slice(0, 4)}-01`, thisMonth())} className="font-mono text-[#085E5E] underline">{l.material_code}</Link>
            <span className="text-neutral-500">{l.material_name}</span>
            {l.self_made && <span className="text-[10px] text-neutral-400">in-house</span>}
          </span>
        </td>
        <td className="px-2 py-1.5 text-right font-mono tabular-nums" title={`${l.usage_samples} samples, ${l.outliers_dropped} outlier(s) left out; range ${num(l.usage_min, 4)}–${num(l.usage_max, 4)}`}>{num(l.std_usage, 4)}</td>
        <td className="px-2 py-1.5 text-right font-mono tabular-nums text-neutral-500">{l.bom_qty_per != null ? num(l.bom_qty_per, 4) : '—'}</td>
        <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', dev != null && Math.abs(dev) > 0.05 && 'font-semibold text-amber-800')}>{dev != null ? `${(dev * 100).toFixed(0)}%` : ''}</td>
        <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(l.std_price, 4)}</td>
        <td className="px-2 py-1.5 text-[11px] text-neutral-500">{l.price_basis}</td>
        <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(l.std_cost, 4)}</td>
      </tr>
      {open && l.child && <CardRows c={l.child} depth={depth + 1} />}
    </>
  )
}

function csvEscape(v: unknown) {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export default function StandardCostPage() {
  const user = useAuthStore((s) => s.user)
  const navigate = useNavigate()
  const { material: pathMaterial } = useParams()
  const [params] = useSearchParams()
  const material = (pathMaterial ?? '').toUpperCase()
  const from = params.get('from') ?? `${thisMonth().slice(0, 4)}-01`
  const to = params.get('to') ?? thisMonth()
  const [draft, setDraft] = useState(material)
  const [dFrom, setDFrom] = useState(from)
  const [dTo, setDTo] = useState(to)
  const [win, setWin] = useState(6)
  const [k, setK] = useState(3)
  const [tol, setTol] = useState(5)
  const [stop, setStop] = useState(true)
  useEffect(() => { setDraft(material); setDFrom(from); setDTo(to) }, [material, from, to])

  const qs = `material=${encodeURIComponent(material)}&from=${from}&to=${to}&window_months=${win}&mad_k=${k}&bom_tolerance=${tol / 100}&exclude_stoppage=${stop}`
  const { data, error, isFetching } = useQuery({
    queryKey: ['cost-standard', qs],
    queryFn: () => financeApi.get<Resp>(`/cost-analysis/standard-cost?${qs}`),
    enabled: !!material && from <= to,
  })
  if (!user) return <Navigate to="/login" replace />

  const exportCsv = () => {
    if (!data) return
    const out: unknown[][] = [['Level', 'Product', 'Input', 'Name', 'Std usage', 'BOM qty', 'BOM dev %', 'Std price', 'Price basis', 'Std cost / unit']]
    const walk = (c: Card, lvl: number) => {
      for (const l of c.lines) {
        out.push([lvl, c.material_code, l.material_code, l.material_name, l.std_usage, l.bom_qty_per,
                  l.bom_deviation != null ? (l.bom_deviation * 100).toFixed(1) : '', l.std_price, l.price_basis, l.std_cost])
        if (l.child) walk(l.child, lvl + 1)
      }
      out.push([lvl, c.material_code, 'CONVERSION', '', '', '', '', '', 'NC basis per unit', c.conversion.unit])
      out.push([lvl, c.material_code, 'STANDARD', '', '', '', '', '', '', c.std_unit])
    }
    walk(data.card, 0)
    const csv = out.map((r) => r.map(csvEscape).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a'); a.href = url; a.download = `standard-cost-${material}-${to}.csv`; a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <PortalChromeLayout activeKey="portal:/finance/analysis/standard" title="Standard Cost"
                        subtitle="AI Financial Analysis · a suggested standard cost model — nothing is written to NC">
      <div className="mx-auto max-w-7xl">
        <SyncBar />
        <form className="mb-2 flex flex-wrap items-center gap-2 text-sm"
              onSubmit={(e) => { e.preventDefault(); const c = draft.trim().toUpperCase(); if (c && dFrom <= dTo) navigate(`/finance/analysis/standard/${encodeURIComponent(c)}?from=${dFrom}&to=${dTo}`) }}>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Product, e.g. S0104" className="w-56 rounded-lg border border-neutral-300 px-2.5 py-1 font-mono" />
          <label className="text-xs text-neutral-500">From</label>
          <input type="month" value={dFrom} onChange={(e) => setDFrom(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1" />
          <label className="text-xs text-neutral-500">To</label>
          <input type="month" value={dTo} onChange={(e) => setDTo(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1" />
          <button type="submit" disabled={!draft.trim()} className="rounded-lg bg-[#085E5E] px-3 py-1 text-white disabled:opacity-40">Suggest standard</button>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />}
        </form>
        <div className="mb-5 flex flex-wrap items-center gap-3 text-xs text-neutral-600">
          <label>Price window <input type="number" min={1} max={12} value={win} onChange={(e) => setWin(Number(e.target.value) || 6)} className="w-14 rounded border border-neutral-300 px-1 py-0.5" /> months</label>
          <label>Outliers beyond <input type="number" min={1} max={10} step={0.5} value={k} onChange={(e) => setK(Number(e.target.value) || 3)} className="w-14 rounded border border-neutral-300 px-1 py-0.5" /> MAD</label>
          <label>Flag BOM lines beyond ± <input type="number" min={0} max={100} value={tol} onChange={(e) => setTol(Number(e.target.value))} className="w-14 rounded border border-neutral-300 px-1 py-0.5" /> %</label>
          <label className="inline-flex items-center gap-1"><input type="checkbox" checked={stop} onChange={(e) => setStop(e.target.checked)} /> Leave stoppage cost out of conversion</label>
        </div>

        {!material ? (
          <div className="rounded-lg border border-neutral-200 px-4 py-10 text-center text-sm text-neutral-500">Pick a product.</div>
        ) : error ? <AccessMessage error={error} /> : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /><p className="mt-2 text-xs text-neutral-500">Rolling the standard up level by level…</p></div>
        ) : (
          <>
            <div className="mb-6 rounded-lg border border-[#085E5E]/30 bg-primary-50/40 p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-[#085E5E]">Summary</p>
              <p className="mt-1 text-base font-semibold leading-snug text-neutral-900">{data.summary.headline}</p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-relaxed text-neutral-700">
                {data.summary.because.map((b, i) => <li key={i}>{b}</li>)}
              </ul>
            </div>

            <h2 className="mb-3 border-b border-neutral-200 pb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">Supporting data</h2>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-sm font-semibold text-neutral-800">Suggested standard cost card — per unit of output</p>
              <button onClick={exportCsv} className="inline-flex items-center gap-1 rounded-lg border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50">
                <Download className="h-3.5 w-3.5" /> CSV (opens in Excel)
              </button>
            </div>
            <div className="mb-6 overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full text-xs">
                <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
                  <th className="px-2 py-1.5 font-medium">Input</th>
                  <th className="px-2 py-1.5 text-right font-medium" title="Median of actual consumption per unit of output">Std usage</th>
                  <th className="px-2 py-1.5 text-right font-medium">BOM</th>
                  <th className="px-2 py-1.5 text-right font-medium">vs BOM</th>
                  <th className="px-2 py-1.5 text-right font-medium">Std price</th>
                  <th className="px-2 py-1.5 font-medium">Price from</th>
                  <th className="px-2 py-1.5 text-right font-medium">Std cost</th>
                </tr></thead>
                <tbody><CardRows c={data.card} depth={0} /></tbody>
              </table>
            </div>

            {data.summary.bom_suggestions.length > 0 && (
              <div className="mb-6">
                <p className="mb-2 text-sm font-semibold text-neutral-800">BOM revisions this implies</p>
                <div className="overflow-x-auto rounded-lg border border-neutral-200">
                  <table className="w-full text-xs">
                    <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
                      <th className="px-2 py-1.5 font-medium">Component</th><th className="px-2 py-1.5 font-medium">Finding</th>
                      <th className="px-2 py-1.5 text-right font-medium">BOM</th><th className="px-2 py-1.5 text-right font-medium">Actual (median)</th>
                      <th className="px-2 py-1.5 text-right font-medium">Difference</th>
                    </tr></thead>
                    <tbody>
                      {data.summary.bom_suggestions.map((f) => (
                        <tr key={f.material_code} className="border-t border-neutral-100">
                          <td className="px-2 py-1.5 font-mono">{f.material_code}</td>
                          <td className="px-2 py-1.5">{f.kind}</td>
                          <td className="px-2 py-1.5 text-right font-mono">{f.bom != null ? num(f.bom, 4) : '—'}</td>
                          <td className="px-2 py-1.5 text-right font-mono">{num(f.actual_median, 4)}</td>
                          <td className="px-2 py-1.5 text-right font-mono">{f.deviation != null ? `${(f.deviation * 100).toFixed(1)}%` : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <p className="mb-2 text-sm font-semibold text-neutral-800">2026 replayed against the standard</p>
            <p className="mb-2 text-xs text-neutral-500">Actual − standard = usage + purchased price + in-house input cost + conversion + accounting adjustments. Under standard costing the adjustments column would not reach product cost.</p>
            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full text-xs">
                <thead className="bg-neutral-50 text-neutral-500"><tr>
                  {['Month', 'Output', 'Actual / unit', 'Standard / unit', 'Actual − std', 'Usage', 'Purchased price', 'In-house inputs', 'Conversion', 'Adjustments'].map((h, i) => (
                    <th key={h} className={cn('px-2 py-1.5 font-medium', i === 0 ? 'text-left' : 'text-right')}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {data.simulation.map((r) => (
                    <Fragment key={r.period}>
                      <tr className="border-t border-neutral-100">
                        <td className="px-2 py-1.5 font-mono">{r.period}</td>
                        <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(r.qty, 0)}</td>
                        <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (r.actual_unit ?? 0) < 0 && 'text-red-700')}>{num(r.actual_unit, 4)}</td>
                        <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(r.std_unit, 4)}</td>
                        <td className="px-2 py-1.5 text-right font-mono font-semibold tabular-nums">{num(r.actual - r.standard)}</td>
                        {[r.usage_variance, r.price_variance, r.input_cost_variance, r.conversion_variance, r.adjustments].map((v, i) => (
                          <td key={i} className={cn('px-2 py-1.5 text-right font-mono tabular-nums', Math.abs(v) >= Math.max(0.1 * Math.abs(r.actual - r.standard), 1) && 'font-semibold', v < 0 && 'text-red-700')}>{num(v)}</td>
                        ))}
                      </tr>
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}
