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

import { Checkbox, Input, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
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
      <Tr className="bg-surface-subtle/60">
        <Td className="pr-2 text-fg-muted" style={{ paddingLeft: 26 + depth * 18 }}>Conversion (per unit, NC basis{c.conversion.excluded_stoppage ? ', stoppage left out' : ''})</Td>
        <Td colSpan={5} />
        <Td align="right" className="font-mono tabular-nums">{num(c.conversion.unit, 4)}</Td>
      </Tr>
      <Tr>
        <Td className="pr-2 font-semibold" style={{ paddingLeft: 26 + depth * 18 }}>Standard {c.material_code}</Td>
        <Td colSpan={5} className="text-2xs text-fg-muted">usage from {c.samples} {c.usage_basis}</Td>
        <Td align="right" className="font-mono font-semibold tabular-nums">{num(c.std_unit, 4)}</Td>
      </Tr>
    </>
  )
}

function LineRow({ l, depth }: { l: Line; depth: number }) {
  const [open, setOpen] = useState(false)
  const dev = l.bom_deviation
  return (
    <>
      <Tr>
        <Td className="pr-2" style={{ paddingLeft: 8 + depth * 18 }}>
          <span className="inline-flex items-center gap-1">
            {l.child ? (
              <Button variant="subtle" size="xs" onClick={() => setOpen(!open)}>
                {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              </Button>
            ) : <span className="inline-block w-3.5" />}
            <Link to={traceLink(l.material_code, `${thisMonth().slice(0, 4)}-01`, thisMonth())} className="font-mono text-primary-700 underline">{l.material_code}</Link>
            <span className="text-fg-muted">{l.material_name}</span>
            {l.self_made && <span className="text-2xs text-fg-muted">in-house</span>}
          </span>
        </Td>
        <Td align="right" className="font-mono tabular-nums" title={`${l.usage_samples} samples, ${l.outliers_dropped} outlier(s) left out; range ${num(l.usage_min, 4)}–${num(l.usage_max, 4)}`}>{num(l.std_usage, 4)}</Td>
        <Td align="right" className="font-mono tabular-nums text-fg-muted">{l.bom_qty_per != null ? num(l.bom_qty_per, 4) : '—'}</Td>
        <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', dev != null && Math.abs(dev) > 0.05 && 'font-semibold text-warning-fg')}>{dev != null ? `${(dev * 100).toFixed(0)}%` : ''}</Td>
        <Td align="right" className="font-mono tabular-nums">{num(l.std_price, 4)}</Td>
        <Td className="text-2xs text-fg-muted">{l.price_basis}</Td>
        <Td align="right" className="font-mono tabular-nums">{num(l.std_cost, 4)}</Td>
      </Tr>
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
          <Input inputSize="sm" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Product, e.g. S0104" className="w-56 font-mono" />
          <label className="text-xs text-fg-muted">From</label>
          <Input inputSize="sm" type="month" value={dFrom} onChange={(e) => setDFrom(e.target.value)} />
          <label className="text-xs text-fg-muted">To</label>
          <Input inputSize="sm" type="month" value={dTo} onChange={(e) => setDTo(e.target.value)} />
          <Button size="xs" type="submit" disabled={!draft.trim()}>Suggest standard</Button>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-fg-muted" />}
        </form>
        <div className="mb-5 flex flex-wrap items-center gap-3 text-xs text-fg-secondary">
          <label>Price window <Input inputSize="sm" type="number" min={1} max={12} value={win} onChange={(e) => setWin(Number(e.target.value) || 6)} className="w-14" /> months</label>
          <label>Outliers beyond <Input inputSize="sm" type="number" min={1} max={10} step={0.5} value={k} onChange={(e) => setK(Number(e.target.value) || 3)} className="w-14" /> MAD</label>
          <label>Flag BOM lines beyond ± <Input inputSize="sm" type="number" min={0} max={100} value={tol} onChange={(e) => setTol(Number(e.target.value))} className="w-14" /> %</label>
          <label className="inline-flex items-center gap-1"><Checkbox checked={stop} onChange={(e) => setStop(e.target.checked)} /> Leave stoppage cost out of conversion</label>
        </div>

        {!material ? (
          <div className="rounded-lg border border-line px-4 py-10 text-center text-sm text-fg-muted">Pick a product.</div>
        ) : error ? <AccessMessage error={error} /> : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /><p className="mt-2 text-xs text-fg-muted">Rolling the standard up level by level…</p></div>
        ) : (
          <>
            <div className="mb-6 rounded-lg border border-primary-700/30 bg-primary-50/40 p-4">
              <p className="text-xs font-semibold text-primary-700">Summary</p>
              <p className="mt-1 text-base font-semibold leading-snug text-fg">{data.summary.headline}</p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-relaxed text-fg-secondary">
                {data.summary.because.map((b, i) => <li key={i}>{b}</li>)}
              </ul>
            </div>

            <h2 className="mb-3 border-b border-line pb-1 text-xs font-semibold text-fg-muted">Supporting data</h2>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-sm font-semibold text-fg">Suggested standard cost card — per unit of output</p>
              <Button variant="secondary" size="xs" onClick={exportCsv}>
                <Download className="h-3.5 w-3.5" /> CSV (opens in Excel)
              </Button>
            </div>
            <div className="mb-6 overflow-x-auto rounded-lg border border-line">
              <Table density="compact">
                <THead><Tr>
                  <Th>Input</Th>
                  <Th align="right" title="Median of actual consumption per unit of output">Std usage</Th>
                  <Th align="right">BOM</Th>
                  <Th align="right">vs BOM</Th>
                  <Th align="right">Std price</Th>
                  <Th>Price from</Th>
                  <Th align="right">Std cost</Th>
                </Tr></THead>
                <TBody><CardRows c={data.card} depth={0} /></TBody>
              </Table>
            </div>

            {data.summary.bom_suggestions.length > 0 && (
              <div className="mb-6">
                <p className="mb-2 text-sm font-semibold text-fg">BOM revisions this implies</p>
                <div className="overflow-x-auto rounded-lg border border-line">
                  <Table density="compact">
                    <THead><Tr>
                      <Th>Component</Th><Th>Finding</Th>
                      <Th align="right">BOM</Th><Th align="right">Actual (median)</Th>
                      <Th align="right">Difference</Th>
                    </Tr></THead>
                    <TBody>
                      {data.summary.bom_suggestions.map((f) => (
                        <Tr key={f.material_code}>
                          <Td className="font-mono">{f.material_code}</Td>
                          <Td>{f.kind}</Td>
                          <Td align="right" className="font-mono">{f.bom != null ? num(f.bom, 4) : '—'}</Td>
                          <Td align="right" className="font-mono">{num(f.actual_median, 4)}</Td>
                          <Td align="right" className="font-mono">{f.deviation != null ? `${(f.deviation * 100).toFixed(1)}%` : '—'}</Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                </div>
              </div>
            )}

            <p className="mb-2 text-sm font-semibold text-fg">2026 replayed against the standard</p>
            <p className="mb-2 text-xs text-fg-muted">Actual − standard = usage + purchased price + in-house input cost + conversion + accounting adjustments. Under standard costing the adjustments column would not reach product cost.</p>
            <div className="overflow-x-auto rounded-lg border border-line">
              <Table density="compact">
                <THead><Tr>
                  {['Month', 'Output', 'Actual / unit', 'Standard / unit', 'Actual − std', 'Usage', 'Purchased price', 'In-house inputs', 'Conversion', 'Adjustments'].map((h, i) => (
                    <Th key={h} className={cn('px-2 py-1.5 font-medium', i === 0 ? 'text-left' : 'text-right')}>{h}</Th>
                  ))}
                </Tr></THead>
                <TBody>
                  {data.simulation.map((r) => (
                    <Fragment key={r.period}>
                      <Tr>
                        <Td className="font-mono">{r.period}</Td>
                        <Td align="right" className="font-mono tabular-nums">{num(r.qty, 0)}</Td>
                        <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (r.actual_unit ?? 0) < 0 && 'text-danger-fg')}>{num(r.actual_unit, 4)}</Td>
                        <Td align="right" className="font-mono tabular-nums">{num(r.std_unit, 4)}</Td>
                        <Td align="right" className="font-mono font-semibold tabular-nums">{num(r.actual - r.standard)}</Td>
                        {[r.usage_variance, r.price_variance, r.input_cost_variance, r.conversion_variance, r.adjustments].map((v, i) => (
                          <Td key={i} className={cn('px-2 py-1.5 text-right font-mono tabular-nums', Math.abs(v) >= Math.max(0.1 * Math.abs(r.actual - r.standard), 1) && 'font-semibold', v < 0 && 'text-danger-fg')}>{num(v)}</Td>
                        ))}
                      </Tr>
                    </Fragment>
                  ))}
                </TBody>
              </Table>
            </div>
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}
