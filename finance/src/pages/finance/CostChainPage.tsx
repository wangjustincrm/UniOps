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

import { Input, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
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
  materials: 'bg-primary-700', conversion: 'bg-info-solid', adjustments: 'bg-warning-solid', unattributable: 'bg-fg-faint',
}

function CompositionSection({ material, period }: { material: string; period: string }) {
  const { data, error } = useQuery({
    queryKey: ['cost-composition', material, period],
    queryFn: () => financeApi.get<Composition>(`/cost-analysis/composition?material=${encodeURIComponent(material)}&period=${period}`),
  })
  if (error) return <AccessMessage error={error} />
  if (!data) return <p className="flex items-center gap-1 py-3 text-xs text-fg-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Drilling the cost down…</p>
  if (!data.available) return <p className="mb-6 text-sm text-fg-muted">{data.headline}</p>
  const pos = (data.buckets ?? []).filter((b) => b.amount > 0)
  const posTotal = pos.reduce((s, b) => s + b.amount, 0)
  return (
    <div className="mb-6">
      <p className="mb-2 text-sm font-semibold text-fg">What a unit of {data.material} is made of ({data.period}) — down to purchased materials</p>
      <div className="mb-3 rounded-lg border border-line bg-surface p-3">
        <p className="text-sm font-semibold text-fg">{data.headline}</p>
        {(data.because ?? []).length > 0 && (
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm leading-relaxed text-fg-secondary">
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
        <div className="overflow-x-auto rounded-lg border border-line">
          <Table density="compact">
            <THead><Tr>
              <Th>Component</Th>
              <Th align="right">Per unit</Th>
              <Th align="right">Share</Th>
              <Th align="right">{data.compare ? `Per unit ${data.compare.period}` : ''}</Th>
            </Tr></THead>
            <TBody>
              {(data.buckets ?? []).map((b) => (
                <Tr key={b.bucket}>
                  <Td><span className={cn('mr-1.5 inline-block h-2 w-2 rounded-sm', GROUP_CLS[b.group])} />{b.bucket}</Td>
                  <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', b.amount < 0 && 'text-danger-fg')}>{num(b.per_unit, 4)}</Td>
                  <Td align="right" className="font-mono tabular-nums text-fg-muted">{b.share != null ? `${(b.share * 100).toFixed(1)}%` : ''}</Td>
                  <Td align="right" className="font-mono tabular-nums text-fg-muted">{data.compare ? num(data.compare.per_unit[b.bucket] ?? 0, 4) : ''}</Td>
                </Tr>
              ))}
              <Tr className="font-semibold">
                <Td>NC unit cost</Td>
                <Td align="right" className="font-mono tabular-nums">{num(data.unit, 4)}</Td>
                <Td />
                <Td align="right" className="font-mono tabular-nums">{data.compare ? num(data.compare.unit, 4) : ''}</Td>
              </Tr>
            </TBody>
          </Table>
        </div>
        <div className="overflow-x-auto rounded-lg border border-line">
          <Table density="compact">
            <THead><Tr>
              <Th>Purchased material (all levels)</Th>
              <Th>Component</Th>
              <Th align="right">Per unit of {data.material}</Th>
            </Tr></THead>
            <TBody>
              {(data.leaves ?? []).slice(0, 12).map((l) => (
                <Tr key={l.material_code}>
                  <Td><span className="font-mono">{l.material_code}</span> <span className="text-fg-muted">{l.material_name}</span></Td>
                  <Td className="text-fg-muted">{l.bucket}</Td>
                  <Td align="right" className="font-mono tabular-nums">{num(l.per_unit_of_product, 4)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      </div>
      <p className="mt-2 text-2xs text-fg-muted">{data.note} Ties out to NC within {num(Math.abs(data.tie_out ?? 0), 2)}.{data.a9 ? ' A9: part of the cost could not be traced (see above).' : ''}</p>
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
      <Tr className={cn('border-t border-line-subtle', n.abnormal && 'bg-danger-soft/60')}>
        <Td className="pr-2" style={{ paddingLeft: 8 + depth * 18 }}>
          <span className="inline-flex items-center gap-1">
            {kids ? (
              <Button variant="subtle" size="xs" onClick={() => setOpen(!open)}>
                {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              </Button>
            ) : <span className="inline-block w-3.5" />}
            <Link to={traceLink(n.material_code, prevMonth(n.period), n.period)} className="font-mono text-primary-700 underline">{n.material_code}</Link>
            <span className="text-fg-muted">{n.material_name}</span>
            {n.abnormal && <span className="rounded border border-danger-line bg-surface px-1 text-2xs font-medium text-danger-fg">abnormal</span>}
            {!n.made_in_house && <span className="text-2xs text-fg-muted">purchased</span>}
          </span>
        </Td>
        <Td className="font-mono text-fg-muted">{n.made_in_house ? n.period : ''}</Td>
        <Td align="right" className="font-mono tabular-nums">{c ? qty(c.qty) : ''}</Td>
        <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (c?.amount ?? 0) < 0 && 'text-danger-fg')}>{c ? num(c.amount) : ''}</Td>
        <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (c?.unit ?? 0) < 0 && 'text-danger-fg')}>{c ? num(c.unit, 4) : ''}</Td>
        <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums', (c?.effective_unit ?? 0) < 0 && 'text-danger-fg',
                          c && c.effective_unit != null && c.unit != null && Math.abs(c.effective_unit - c.unit) > 0.0001 && 'font-semibold')}>
          {c ? num(c.effective_unit, 4) : ''}
        </Td>
        <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums font-semibold', (n.unit ?? 0) <= 0 && n.unit != null && 'text-danger-fg')}>
          {n.unit != null ? num(n.unit, 4) : ''}
        </Td>
        <Td align="right" className="font-mono text-fg-muted">{n.previous_unit != null ? `${num(n.previous_unit, 4)} (${n.previous_period})` : ''}</Td>
        <Td className="text-2xs text-fg-muted">
          {n.breakdown && Object.entries(n.breakdown).filter(([, v]) => Math.abs(v) >= 0.5).map(([k, v]) => (
            <span key={k} className={cn('mr-2', v < 0 && 'text-danger-fg')}>{BREAK_LABELS[k]} {num(v, 0)}</span>
          ))}
        </Td>
      </Tr>
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
          <Input inputSize="sm" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Finished good, e.g. S0104"
                 className="w-56 font-mono" />
          <Input inputSize="sm" type="month" value={dPeriod} onChange={(e) => setDPeriod(e.target.value)} />
          <Button size="xs" type="submit" disabled={!draft.trim()}>Show chain</Button>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-fg-muted" />}
        </form>

        {!material ? (
          <div className="rounded-lg border border-line px-4 py-10 text-center text-sm text-fg-muted">Pick a product and a month.</div>
        ) : error ? <AccessMessage error={error} /> : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : (
          <>
            <div className={cn('mb-6 rounded-lg border p-4', data.abnormal.length ? 'border-primary-700/30 bg-primary-50/40' : 'border-line bg-surface-subtle')}>
              <p className="text-xs font-semibold text-primary-700">Summary</p>
              <p className="mt-1 text-sm font-semibold leading-relaxed text-fg">{data.headline}</p>
            </div>
            <h2 className="mb-3 border-b border-line pb-1 text-xs font-semibold text-fg-muted">Supporting data</h2>
            <CompositionSection material={material} period={period} />
            <p className="mb-2 text-sm font-semibold text-fg">The chain, level by level</p>
            <div className="overflow-x-auto rounded-lg border border-line">
              <Table density="compact">
                <THead><Tr>
                  <Th>Material</Th>
                  <Th>Made in</Th>
                  <Th align="right">Qty used</Th>
                  <Th align="right">Value used</Th>
                  <Th align="right">Unit on issue</Th>
                  <Th align="right" title="Issue plus the settlement / tail adjustments NC posted onto it">Unit incl. adj.</Th>
                  <Th align="right">Own unit cost</Th>
                  <Th align="right">Previous normal</Th>
                  <Th>Own cost made of</Th>
                </Tr></THead>
                <TBody><TreeNode n={data.tree} depth={0} onPath={onPath} /></TBody>
              </Table>
            </div>
            <p className="mt-2 text-2xs text-fg-muted">{data.note}</p>
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}
