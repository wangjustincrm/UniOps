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
import { Button, Input, SortTh, TBody, THead, Table, Td, Tr, useSortableRows } from '@uniops/shell'
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
                         off ? 'border-danger-line bg-danger-soft/60' : 'border-line bg-surface')}>
        {n.children.length > 0 ? (
          <Button variant="subtle" size="xs" onClick={() => setOpen(!open)}>
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </Button>
        ) : <span className="inline-block w-3.5" />}
        <Link to={traceLink(n.material_code, n.period, n.period)} className="font-mono text-primary-700 underline">{n.material_code}</Link>
        <span className="font-mono text-fg-secondary">batch {n.batch}</span>
        <span className="text-fg-muted">{n.period} · {qty(n.qty)}{n.basis === 'month' ? ' · month basis (no production order)' : ` · order ${n.mo_codes.join(', ')}`}</span>
        <span className="ml-auto font-mono">NC <b className={cn((n.nc.unit ?? 0) < 0 && 'text-danger-fg')}>{num(n.nc.unit, 4)}</b></span>
        <span className="font-mono">actual <b>{num(n.actual.unit, 4)}</b></span>
        {off && <span className="font-mono text-danger-fg">Δ {num(n.variance)}</span>}
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
  if (!data) return <p className="flex items-center gap-1 py-3 text-xs text-fg-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Costing the batch…</p>
  const b = data.batch
  return (
    <div className="space-y-3 py-2">
      <div className="rounded-lg border border-primary-700/30 bg-primary-50/40 p-3">
        <p className="text-sm font-semibold leading-relaxed text-fg">{data.summary.headline}</p>
        <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm leading-relaxed text-fg-secondary">
          {data.summary.because.map((x, i) => <li key={i}>{x}</li>)}
        </ul>
        <Button variant="link" onClick={() => setEv(!ev)} className="mt-2 text-xs">
          {ev ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />} Evidence ({data.summary.evidence.length})
        </Button>
        {ev && (
          <Table density="compact" className="mt-1"><TBody>
            {data.summary.evidence.map((e, i) => (
              <Tr key={i}>
                <Td className="w-1/3 pr-3 text-fg-muted">{e.label}</Td>
                <Td className="pr-3 font-mono text-fg">{e.value}</Td>
                <Td className="font-mono text-2xs text-fg-muted">{e.ref ?? ''}</Td>
              </Tr>
            ))}
          </TBody></Table>
        )}
      </div>
      <div>
        <p className="text-xs font-medium text-fg-secondary">Batch genealogy — NC's unit cost against the actual, batch by batch</p>
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
  // Whole list; each batch and its open detail row share one keyed Fragment.
  const { rows: batches, sort, onSort } = useSortableRows(data?.batches, {
    batch: (b) => b.batch, month: (b) => b.period, qty: (b) => b.qty,
    nc_unit: (b) => b.nc_unit, actual_unit: (b) => b.actual_unit, variance: (b) => b.variance,
    basis: (b) => (b.basis === 'month' ? 'month (no order)' : `order ${b.mo_codes.join(', ')}`),
  })
  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout activeKey="portal:/finance/analysis/batches" title="Batch Cost"
                        subtitle="AI Financial Analysis · each batch from its own production order — a figure NC does not have">
      <div className="mx-auto max-w-7xl">
        <SyncBar />
        <form className="mb-5 flex flex-wrap items-center gap-2 text-sm"
              onSubmit={(e) => { e.preventDefault(); const c = draft.trim().toUpperCase(); if (c && dFrom <= dTo) navigate(`/finance/analysis/batches/${encodeURIComponent(c)}?from=${dFrom}&to=${dTo}`) }}>
          <Input inputSize="sm" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Product, e.g. S0104"
                 className="w-56 font-mono" aria-label="Product" />
          <label className="text-xs text-fg-muted">From</label>
          <Input inputSize="sm" type="month" value={dFrom} onChange={(e) => setDFrom(e.target.value)} className="w-40" aria-label="From" />
          <label className="text-xs text-fg-muted">To</label>
          <Input inputSize="sm" type="month" value={dTo} onChange={(e) => setDTo(e.target.value)} className="w-40" aria-label="To" />
          <Button size="xs" type="submit" disabled={!draft.trim()}>Show batches</Button>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-fg-muted" />}
        </form>
        {!material ? (
          <div className="rounded-lg border border-line px-4 py-10 text-center text-sm text-fg-muted">Pick a product.</div>
        ) : error ? <AccessMessage error={error} /> : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : (
          <>
            <div className="mb-6 rounded-lg border border-primary-700/30 bg-primary-50/40 p-4">
              <p className="text-xs font-semibold text-primary-700">Summary</p>
              <p className="mt-1 text-base font-semibold leading-snug text-fg">{data.headline}</p>
              {data.receipts_without_batch > 0 && <p className="mt-1 text-xs text-fg-secondary">{data.receipts_without_batch} completion receipt line(s) carry no batch number and are not listed.</p>}
            </div>
            <h2 className="mb-3 border-b border-line pb-1 text-xs font-semibold text-fg-muted">Batches — click one for its analysis</h2>
            <div className="overflow-x-auto rounded-lg border border-line">
              <Table density="compact">
                <THead><Tr>
                  <SortTh label="Batch" sortKey="batch" sort={sort} onSort={onSort} className="px-2 py-1.5 font-medium" />
                  <SortTh label="Month" sortKey="month" sort={sort} onSort={onSort} className="px-2 py-1.5 font-medium" />
                  <SortTh label="Qty" sortKey="qty" sort={sort} onSort={onSort} align="right" className="px-2 py-1.5 font-medium" />
                  <SortTh label="NC unit cost" sortKey="nc_unit" sort={sort} onSort={onSort} align="right" className="px-2 py-1.5 font-medium" />
                  <SortTh label="Actual unit cost" sortKey="actual_unit" sort={sort} onSort={onSort} align="right" className="px-2 py-1.5 font-medium" />
                  <SortTh label="NC − actual" sortKey="variance" sort={sort} onSort={onSort} align="right" className="px-2 py-1.5 font-medium" />
                  <SortTh label="Basis" sortKey="basis" sort={sort} onSort={onSort} className="px-2 py-1.5 font-medium" />
                </Tr></THead>
                <TBody>
                  {batches.map((b) => {
                    const off = Math.abs(b.variance) >= Math.max(0.005 * Math.abs((b.actual_unit ?? 0) * b.qty), 1)
                    return (
                      <Fragment key={b.batch}>
                        <Tr onClick={() => setOpen(open === b.batch ? null : b.batch)}
                            className={cn('cursor-pointer border-t border-line-subtle hover:bg-surface-subtle', off && 'bg-danger-soft/50')}>
                          <Td className="font-mono">{open === b.batch ? '▾' : '▸'} {b.batch}</Td>
                          <Td className="font-mono">{b.period}</Td>
                          <Td align="right" className="font-mono tabular-nums">{qty(b.qty)}</Td>
                          <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (b.nc_unit ?? 0) < 0 && 'text-danger-fg')}>{num(b.nc_unit, 4)}</Td>
                          <Td align="right" className="font-mono tabular-nums">{num(b.actual_unit, 4)}</Td>
                          <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', off && 'font-semibold text-danger-fg')}>{num(b.variance)}</Td>
                          <Td className="text-fg-muted">{b.basis === 'month' ? 'month (no order)' : `order ${b.mo_codes.join(', ')}`}</Td>
                        </Tr>
                        {open === b.batch && (
                          <Tr><Td colSpan={7} className="bg-surface-subtle/60"><BatchDetail material={material} batch={b.batch} /></Td></Tr>
                        )}
                      </Fragment>
                    )
                  })}
                </TBody>
              </Table>
            </div>
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}
