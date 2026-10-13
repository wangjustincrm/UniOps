/**
 * AI Financial Analysis — Cost Chain (spec §8).
 *
 * One finished good, one month: the tree of NC's own product cost down to
 * purchased materials, each self-made input at the month it was produced in.
 * The conclusion first — the deepest abnormal node and its root cause — then
 * the tree.
 */
import { useEffect, useState } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccessMessage, num, qty, SyncBar, thisMonth, traceLink } from '@/lib/costAnalysis'

interface Node {
  material_code: string; material_name: string | null; period: string; made_in_house: boolean
  qty?: number; amount?: number; unit?: number | null; abnormal?: boolean
  previous_unit?: number | null; previous_period?: string | null; truncated?: boolean
  breakdown?: Record<string, number>
  consumed: { qty: number | null; amount: number | null; unit: number | null; effective_unit: number | null
              settlement_adj: number | null; tail_adj: number | null; share: number | null } | null
  children: Node[]
}
interface Resp { material: string; period: string; headline: string; note: string
                 abnormal: { material_code: string; period: string; path: string[] }[]; tree: Node }

const BREAK_LABELS: Record<string, string> = {
  consumed_total: 'materials', conversion_total: 'conversion', settlement_total: 'settlement adj.',
  tail_total: 'tail adj.', carried_total: 'carried in',
}

interface Bucket { bucket: string; group: string; amount: number; per_unit: number | null; share: number | null }
interface Composition {
  available: boolean; material: string; period: string; qty?: number; total?: number; unit?: number | null
  buckets?: Bucket[]; leaves?: { material_code: string; material_name: string | null; bucket: string; amount: number; per_unit_of_product: number | null }[]
  compare?: { period: string; per_unit: Record<string, number>; unit: number } | null
  tie_out?: number; a9?: boolean; headline: string; because?: string[]; note?: string
}

const GROUP_CLS: Record<string, string> = {
  materials: 'bg-[#085E5E]', conversion: 'bg-sky-500', adjustments: 'bg-amber-500', unattributable: 'bg-neutral-400',
}

function CompositionSection({ material, period }: { material: string; period: string }) {
  const { data, error } = useQuery({
    queryKey: ['cost-composition', material, period],
    queryFn: () => financeApi.get<Composition>(`/cost-analysis/composition?material=${encodeURIComponent(material)}&period=${period}`),
  })
  if (error) return <AccessMessage error={error} />
  if (!data) return <p className="flex items-center gap-1 py-3 text-xs text-neutral-500"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Drilling the cost down…</p>
  if (!data.available) return <p className="mb-6 text-sm text-neutral-500">{data.headline}</p>
  const pos = (data.buckets ?? []).filter((b) => b.amount > 0)
  const posTotal = pos.reduce((s, b) => s + b.amount, 0)
  return (
    <div className="mb-6">
      <p className="mb-2 text-sm font-semibold text-neutral-800">What a unit of {data.material} is made of ({data.period}) — down to purchased materials</p>
      <div className="mb-3 rounded-lg border border-neutral-200 bg-white p-3">
        <p className="text-sm font-semibold text-neutral-900">{data.headline}</p>
        {(data.because ?? []).length > 0 && (
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm leading-relaxed text-neutral-700">
            {data.because!.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        )}
        {posTotal > 0 && (
          <div className="mt-3 flex h-3 w-full overflow-hidden rounded" title="Positive components only">
            {pos.map((b) => <div key={b.bucket} className={GROUP_CLS[b.group]} style={{ width: `${(b.amount / posTotal) * 100}%`, opacity: 0.45 + 0.55 * (b.amount / posTotal) }} title={`${b.bucket} ${num(b.per_unit, 4)}/unit`} />)}
          </div>
        )}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="overflow-x-auto rounded-lg border border-neutral-200">
          <table className="w-full text-xs">
            <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
              <th className="px-2 py-1.5 font-medium">Component</th>
              <th className="px-2 py-1.5 text-right font-medium">Per unit</th>
              <th className="px-2 py-1.5 text-right font-medium">Share</th>
              <th className="px-2 py-1.5 text-right font-medium">{data.compare ? `Per unit ${data.compare.period}` : ''}</th>
            </tr></thead>
            <tbody>
              {(data.buckets ?? []).map((b) => (
                <tr key={b.bucket} className="border-t border-neutral-100">
                  <td className="px-2 py-1.5"><span className={cn('mr-1.5 inline-block h-2 w-2 rounded-sm', GROUP_CLS[b.group])} />{b.bucket}</td>
                  <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', b.amount < 0 && 'text-red-700')}>{num(b.per_unit, 4)}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-neutral-500">{b.share != null ? `${(b.share * 100).toFixed(1)}%` : ''}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-neutral-500">{data.compare ? num(data.compare.per_unit[b.bucket] ?? 0, 4) : ''}</td>
                </tr>
              ))}
              <tr className="border-t border-neutral-200 font-semibold">
                <td className="px-2 py-1.5">NC unit cost</td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(data.unit, 4)}</td>
                <td />
                <td className="px-2 py-1.5 text-right font-mono tabular-nums">{data.compare ? num(data.compare.unit, 4) : ''}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="overflow-x-auto rounded-lg border border-neutral-200">
          <table className="w-full text-xs">
            <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
              <th className="px-2 py-1.5 font-medium">Purchased material (all levels)</th>
              <th className="px-2 py-1.5 font-medium">Component</th>
              <th className="px-2 py-1.5 text-right font-medium">Per unit of {data.material}</th>
            </tr></thead>
            <tbody>
              {(data.leaves ?? []).slice(0, 12).map((l) => (
                <tr key={l.material_code} className="border-t border-neutral-100">
                  <td className="px-2 py-1.5"><span className="font-mono">{l.material_code}</span> <span className="text-neutral-500">{l.material_name}</span></td>
                  <td className="px-2 py-1.5 text-neutral-500">{l.bucket}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums">{num(l.per_unit_of_product, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <p className="mt-2 text-[11px] text-neutral-500">{data.note} Ties out to NC within {num(Math.abs(data.tie_out ?? 0), 2)}.{data.a9 ? ' A9: part of the cost could not be traced (see above).' : ''}</p>
    </div>
  )
}

function prevMonth(p: string) {
  const y = Number(p.slice(0, 4)); const m = Number(p.slice(5, 7))
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`
}

function TreeNode({ n, depth, onPath }: { n: Node; depth: number; onPath: Set<string> }) {
  const [open, setOpen] = useState(depth < 2 || onPath.has(n.material_code))
  const c = n.consumed
  const kids = n.children.length > 0
  return (
    <>
      <tr className={cn('border-t border-neutral-100', n.abnormal && 'bg-red-50/60')}>
        <td className="py-1.5 pr-2" style={{ paddingLeft: 8 + depth * 18 }}>
          <span className="inline-flex items-center gap-1">
            {kids ? (
              <button onClick={() => setOpen(!open)} className="text-neutral-400">
                {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              </button>
            ) : <span className="inline-block w-3.5" />}
            <Link to={traceLink(n.material_code, prevMonth(n.period), n.period)} className="font-mono text-[#085E5E] underline">{n.material_code}</Link>
            <span className="text-neutral-500">{n.material_name}</span>
            {n.abnormal && <span className="rounded border border-red-200 bg-white px-1 text-[10px] font-medium text-red-700">abnormal</span>}
            {!n.made_in_house && <span className="text-[10px] text-neutral-400">purchased</span>}
          </span>
        </td>
        <td className="px-2 py-1.5 font-mono text-neutral-500">{n.made_in_house ? n.period : ''}</td>
        <td className="px-2 py-1.5 text-right font-mono tabular-nums">{c ? qty(c.qty) : ''}</td>
        <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (c?.amount ?? 0) < 0 && 'text-red-700')}>{c ? num(c.amount) : ''}</td>
        <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (c?.unit ?? 0) < 0 && 'text-red-700')}>{c ? num(c.unit, 4) : ''}</td>
        <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (c?.effective_unit ?? 0) < 0 && 'text-red-700',
                          c && c.effective_unit != null && c.unit != null && Math.abs(c.effective_unit - c.unit) > 0.0001 && 'font-semibold')}>
          {c ? num(c.effective_unit, 4) : ''}
        </td>
        <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums font-semibold', (n.unit ?? 0) <= 0 && n.unit != null && 'text-red-700')}>
          {n.unit != null ? num(n.unit, 4) : ''}
        </td>
        <td className="px-2 py-1.5 text-right font-mono text-neutral-500">{n.previous_unit != null ? `${num(n.previous_unit, 4)} (${n.previous_period})` : ''}</td>
        <td className="px-2 py-1.5 text-[11px] text-neutral-500">
          {n.breakdown && Object.entries(n.breakdown).filter(([, v]) => Math.abs(v) >= 0.5).map(([k, v]) => (
            <span key={k} className={cn('mr-2', v < 0 && 'text-red-700')}>{BREAK_LABELS[k]} {num(v, 0)}</span>
          ))}
        </td>
      </tr>
      {open && n.children.map((ch, i) => <TreeNode key={`${ch.material_code}-${i}`} n={ch} depth={depth + 1} onPath={onPath} />)}
    </>
  )
}

export default function CostChainPage() {
  const user = useAuthStore((s) => s.user)
  const navigate = useNavigate()
  const { material: pathMaterial } = useParams()
  const [params] = useSearchParams()
  const material = (pathMaterial ?? '').toUpperCase()
  const period = params.get('period') ?? thisMonth()
  const [draft, setDraft] = useState(material)
  const [dPeriod, setDPeriod] = useState(period)
  useEffect(() => { setDraft(material); setDPeriod(period) }, [material, period])

  const { data, error, isFetching } = useQuery({
    queryKey: ['cost-chain', material, period],
    queryFn: () => financeApi.get<Resp>(`/cost-analysis/chain?material=${encodeURIComponent(material)}&period=${period}`),
    enabled: !!material,
  })
  if (!user) return <Navigate to="/login" replace />
  const onPath = new Set((data?.abnormal ?? []).flatMap((a) => a.path))

  return (
    <PortalChromeLayout activeKey="portal:/finance/analysis/chain" title="Cost Chain"
                        subtitle="AI Financial Analysis · a product's cost, level by level, down to purchased materials">
      <div className="mx-auto max-w-7xl">
        <SyncBar />
        <form className="mb-5 flex flex-wrap items-center gap-2 text-sm"
              onSubmit={(e) => { e.preventDefault(); const c = draft.trim().toUpperCase(); if (c) navigate(`/finance/analysis/chain/${encodeURIComponent(c)}?period=${dPeriod}`) }}>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Finished good, e.g. S0104"
                 className="w-56 rounded-lg border border-neutral-300 px-2.5 py-1 font-mono" />
          <input type="month" value={dPeriod} onChange={(e) => setDPeriod(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1" />
          <button type="submit" disabled={!draft.trim()} className="rounded-lg bg-[#085E5E] px-3 py-1 text-white disabled:opacity-40">Show chain</button>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />}
        </form>

        {!material ? (
          <div className="rounded-lg border border-neutral-200 px-4 py-10 text-center text-sm text-neutral-500">Pick a product and a month.</div>
        ) : error ? <AccessMessage error={error} /> : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <>
            <div className={cn('mb-6 rounded-lg border p-4', data.abnormal.length ? 'border-[#085E5E]/30 bg-primary-50/40' : 'border-neutral-200 bg-neutral-50')}>
              <p className="text-xs font-semibold uppercase tracking-wide text-[#085E5E]">Summary</p>
              <p className="mt-1 text-sm font-semibold leading-relaxed text-neutral-900">{data.headline}</p>
            </div>
            <h2 className="mb-3 border-b border-neutral-200 pb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">Supporting data</h2>
            <CompositionSection material={material} period={period} />
            <p className="mb-2 text-sm font-semibold text-neutral-800">The chain, level by level</p>
            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full text-xs">
                <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
                  <th className="px-2 py-1.5 font-medium">Material</th>
                  <th className="px-2 py-1.5 font-medium">Made in</th>
                  <th className="px-2 py-1.5 text-right font-medium">Qty used</th>
                  <th className="px-2 py-1.5 text-right font-medium">Value used</th>
                  <th className="px-2 py-1.5 text-right font-medium">Unit on issue</th>
                  <th className="px-2 py-1.5 text-right font-medium" title="Issue plus the settlement / tail adjustments NC posted onto it">Unit incl. adj.</th>
                  <th className="px-2 py-1.5 text-right font-medium">Own unit cost</th>
                  <th className="px-2 py-1.5 text-right font-medium">Previous normal</th>
                  <th className="px-2 py-1.5 font-medium">Own cost made of</th>
                </tr></thead>
                <tbody><TreeNode n={data.tree} depth={0} onPath={onPath} /></tbody>
              </table>
            </div>
            <p className="mt-2 text-[11px] text-neutral-500">{data.note}</p>
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}
