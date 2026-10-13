/**
 * AI Financial Analysis — Batch Cost (spec §6.6; NC has no such figure).
 *
 * NC costs a product per month, so every batch made in a month carries the
 * same unit cost and any accounting adjustment in that month is spread over
 * them. Here each batch is costed from its own production order's consumption,
 * following self-made inputs to the batches they came from, by batch number.
 * The conclusion first (which batch NC booked furthest from its actual cost,
 * and why), the batches and the genealogy as evidence.
 */
import { Fragment, useEffect, useState } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccessMessage, type Evidence, num, qty, SyncBar, thisMonth, traceLink } from '@/lib/costAnalysis'

interface BatchRow { batch: string; period: string; qty: number; nc_unit: number | null
                     actual_unit: number | null; variance: number; basis: string; mo_codes: string[] }
interface ListResp { material: string; headline: string; batches: BatchRow[]; receipts_without_batch: number }
interface Input { material_code: string; material_name: string | null; batch: string | null; qty: number
                  nc_amount: number; actual_amount: number; actual_unit?: number; source: string }
interface BatchNode {
  material_code: string; material_name: string | null; batch: string; period: string; basis: string
  mo_codes: string[]; qty: number; nc: { amount: number; unit: number | null }
  actual: { amount: number; unit: number | null }; variance: number; conversion?: number
  inputs: Input[]; adjustments: { kind: string; material_code?: string; amount: number; ref: string | null }[]
  children: BatchNode[]; notes: string[]
}
interface DetailResp { batch: BatchNode; summary: { headline: string; because: string[]; evidence: Evidence[] } }

function Genealogy({ n, depth }: { n: BatchNode; depth: number }) {
  const [open, setOpen] = useState(true)
  const off = Math.abs(n.variance) >= Math.max(0.005 * Math.abs(n.actual.amount), 1)
  return (
    <div style={{ marginLeft: depth * 18 }} className="mt-1">
      <div className={cn('flex flex-wrap items-center gap-2 rounded border px-2 py-1 text-xs',
                         off ? 'border-red-200 bg-red-50/60' : 'border-neutral-200 bg-white')}>
        {n.children.length > 0 ? (
          <button onClick={() => setOpen(!open)} className="text-neutral-400">
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : <span className="inline-block w-3.5" />}
        <Link to={traceLink(n.material_code, n.period, n.period)} className="font-mono text-[#085E5E] underline">{n.material_code}</Link>
        <span className="font-mono text-neutral-600">batch {n.batch}</span>
        <span className="text-neutral-500">{n.period} · {qty(n.qty)}{n.basis === 'month' ? ' · month basis (no production order)' : ` · order ${n.mo_codes.join(', ')}`}</span>
        <span className="ml-auto font-mono">NC <b className={cn((n.nc.unit ?? 0) < 0 && 'text-red-700')}>{num(n.nc.unit, 4)}</b></span>
        <span className="font-mono">actual <b>{num(n.actual.unit, 4)}</b></span>
        {off && <span className="font-mono text-red-700">Δ {num(n.variance)}</span>}
      </div>
      {open && n.children.map((c, i) => <Genealogy key={i} n={c} depth={depth + 1} />)}
    </div>
  )
}

function BatchDetail({ material, batch }: { material: string; batch: string }) {
  const { data, error } = useQuery({
    queryKey: ['cost-batch', material, batch],
    queryFn: () => financeApi.get<DetailResp>(`/cost-analysis/batch?material=${encodeURIComponent(material)}&batch=${encodeURIComponent(batch)}`),
  })
  const [ev, setEv] = useState(false)
  if (error) return <AccessMessage error={error} />
  if (!data) return <p className="flex items-center gap-1 py-3 text-xs text-neutral-500"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Costing the batch…</p>
  const b = data.batch
  return (
    <div className="space-y-3 py-2">
      <div className="rounded-lg border border-[#085E5E]/30 bg-primary-50/40 p-3">
        <p className="text-sm font-semibold leading-relaxed text-neutral-900">{data.summary.headline}</p>
        <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm leading-relaxed text-neutral-700">
          {data.summary.because.map((x, i) => <li key={i}>{x}</li>)}
        </ul>
        <button onClick={() => setEv(!ev)} className="mt-2 inline-flex items-center gap-1 text-xs text-[#085E5E]">
          {ev ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />} Evidence ({data.summary.evidence.length})
        </button>
        {ev && (
          <table className="mt-1 w-full text-xs"><tbody>
            {data.summary.evidence.map((e, i) => (
              <tr key={i} className="border-t border-neutral-100">
                <td className="w-1/3 py-1 pr-3 text-neutral-500">{e.label}</td>
                <td className="py-1 pr-3 font-mono text-neutral-800">{e.value}</td>
                <td className="py-1 font-mono text-[11px] text-neutral-400">{e.ref ?? ''}</td>
              </tr>
            ))}
          </tbody></table>
        )}
      </div>
      <div>
        <p className="text-xs font-medium text-neutral-600">Batch genealogy — NC's unit cost against the actual, batch by batch</p>
        <Genealogy n={b} depth={0} />
      </div>
    </div>
  )
}

export default function BatchCostPage() {
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
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => { setDraft(material); setDFrom(from); setDTo(to) }, [material, from, to])

  const { data, error, isFetching } = useQuery({
    queryKey: ['cost-batches', material, from, to],
    queryFn: () => financeApi.get<ListResp>(`/cost-analysis/batches?material=${encodeURIComponent(material)}&from=${from}&to=${to}`),
    enabled: !!material && from <= to,
  })
  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout activeKey="portal:/finance/analysis/batches" title="Batch Cost"
                        subtitle="AI Financial Analysis · each batch from its own production order — a figure NC does not have">
      <div className="mx-auto max-w-7xl">
        <SyncBar />
        <form className="mb-5 flex flex-wrap items-center gap-2 text-sm"
              onSubmit={(e) => { e.preventDefault(); const c = draft.trim().toUpperCase(); if (c && dFrom <= dTo) navigate(`/finance/analysis/batches/${encodeURIComponent(c)}?from=${dFrom}&to=${dTo}`) }}>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Product, e.g. S0104"
                 className="w-56 rounded-lg border border-neutral-300 px-2.5 py-1 font-mono" />
          <label className="text-xs text-neutral-500">From</label>
          <input type="month" value={dFrom} onChange={(e) => setDFrom(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1" />
          <label className="text-xs text-neutral-500">To</label>
          <input type="month" value={dTo} onChange={(e) => setDTo(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1" />
          <button type="submit" disabled={!draft.trim()} className="rounded-lg bg-[#085E5E] px-3 py-1 text-white disabled:opacity-40">Show batches</button>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />}
        </form>
        {!material ? (
          <div className="rounded-lg border border-neutral-200 px-4 py-10 text-center text-sm text-neutral-500">Pick a product.</div>
        ) : error ? <AccessMessage error={error} /> : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <>
            <div className="mb-6 rounded-lg border border-[#085E5E]/30 bg-primary-50/40 p-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-[#085E5E]">Summary</p>
              <p className="mt-1 text-base font-semibold leading-snug text-neutral-900">{data.headline}</p>
              {data.receipts_without_batch > 0 && <p className="mt-1 text-xs text-neutral-600">{data.receipts_without_batch} completion receipt line(s) carry no batch number and are not listed.</p>}
            </div>
            <h2 className="mb-3 border-b border-neutral-200 pb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">Batches — click one for its analysis</h2>
            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full text-xs">
                <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
                  <th className="px-2 py-1.5 font-medium">Batch</th><th className="px-2 py-1.5 font-medium">Month</th>
                  <th className="px-2 py-1.5 text-right font-medium">Qty</th>
                  <th className="px-2 py-1.5 text-right font-medium">NC unit cost</th>
                  <th className="px-2 py-1.5 text-right font-medium">Actual unit cost</th>
                  <th className="px-2 py-1.5 text-right font-medium">NC − actual</th>
                  <th className="px-2 py-1.5 font-medium">Basis</th>
                </tr></thead>
                <tbody>
                  {data.batches.map((b) => {
                    const off = Math.abs(b.variance) >= Math.max(0.005 * Math.abs((b.actual_unit ?? 0) * b.qty), 1)
                    return (
                      <Fragment key={b.batch}>
                        <tr onClick={() => setOpen(open === b.batch ? null : b.batch)}
                            className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-50', off && 'bg-red-50/50')}>
                          <td className="px-2 py-1.5 font-mono">{open === b.batch ? '▾' : '▸'} {b.batch}</td>
                          <td className="px-2 py-1.5 font-mono">{b.period}</td>
                          <td className="px-2 py-1.5 text-right font-mono tabular-nums">{qty(b.qty)}</td>
                          <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (b.nc_unit ?? 0) < 0 && 'text-red-700')}>{num(b.nc_unit, 4)}</td>
                          <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(b.actual_unit, 4)}</td>
                          <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', off && 'font-semibold text-red-700')}>{num(b.variance)}</td>
                          <td className="px-2 py-1.5 text-neutral-500">{b.basis === 'month' ? 'month (no order)' : `order ${b.mo_codes.join(', ')}`}</td>
                        </tr>
                        {open === b.batch && (
                          <tr><td colSpan={7} className="bg-neutral-50/60 px-3"><BatchDetail material={material} batch={b.batch} /></td></tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}
