/**
 * AI Financial Analysis — Material Trace.
 *
 * One material, a range of months, rebuilt from NC's own ledger (spec §6.1):
 * opening + receipts − issues = closing, held against NC's month-end balance
 * for closed months; NC's product cost for it split into consumed / settlement
 * adjustment / carried in (§6.3); provisional receipts paired with their
 * settlement; issue adjustments with where they landed; who consumed it.
 *
 * Usage is material CONSUMPTION, never production picking (§6.0) — picking
 * only moves stock to the line and shows here as a transfer.
 */
import { Fragment, useEffect, useState } from 'react'
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { SortTh, useSortableRows } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccessMessage, type Diagnosis, DiagnosisPanel, num, OpenPeriodBadge, qty, SyncBar, thisMonth } from '@/lib/costAnalysis'

interface Flow { qty: number | null; amount: number | null; label: string }
interface Month {
  period: string; opening: { qty: number; amount: number }
  flows: Record<string, Flow>; receipts: { qty: number; amount: number }
  issues: { qty: number; amount: number }
  closing: { qty: number; amount: number; unit: number | null }
  nc_balance: { qty: number; amount: number } | null; period_closed: boolean
  balance_matches: boolean | null; production_unit_cost: number | null
}
interface Settlement {
  material_code: string; batch_code: string | null; settlement_code: string | null
  settlement_period: string
  provisional: { bill_code: string | null; receipt_code: string | null; period: string
                 qty: number; unit: number | null; amount: number } | null
  reversal: { qty: number; amount: number }
  settled: { qty: number; amount: number; unit: number | null }
  variance: number
}
interface OutAdj {
  period: string; bill_code: string | null; amount: number; attached_to: string | null
  mo_code: string | null; batch_code: string | null
  landed_on: { material_code: string; material_name: string | null }[]
}
interface Consumer {
  period: string; cost_object_code: string | null; cost_object_name: string | null
  kind: 'consumption' | 'adjustment'; qty: number | null; amount: number; unit: number | null
}
interface PcInput {
  material_code: string | null; material_name: string | null; element_code: string | null
  element_name: string | null; nc_qty: number | null; nc_amount: number
  consumed_qty: number; consumed_amount: number; adjustment: number; carried_in: number | null
}
interface ProductCost {
  period: string; qty: number; amount: number; unit: number | null
  adjustment_total: number; inputs: PcInput[]
}
interface Line {
  period: string; bill_type: string; bill_code: string | null; biz_date: string | null
  category: string; category_label: string | null; direction: 'in' | 'out'
  trantype: string | null; qty: number | null; price: number | null; amount: number
  batch_code: string | null; mo_code: string | null; src_code: string | null; estimate: boolean
}
interface VoucherRow {
  bill_code: string | null; document: string; period: string; document_amount: number | null
  voucher: { voucher_no: string | null; period: string | null; date: string | null
             explanation: string | null; status: string; status_label: string
             amount_on_voucher: number | null } | null
}
interface Trace {
  material: { code: string; name: string | null }; months: Month[]
  settlements: Settlement[]; out_adjustments: OutAdj[]; consumers: Consumer[]
  product_cost: ProductCost[]; lines: Line[]; diagnosis?: Diagnosis
  vouchers: VoucherRow[]; vouchers_unposted: number
}

// Display order of the flow columns; transfers and other in/out get their own net columns.
const FLOW_COLS: [string, string][] = [
  ['purchase_provisional', 'Provisional'], ['provisional_reversal', 'Prov. reversal'],
  ['settlement', 'Settlement'], ['purchase', 'Purchase'], ['production', 'Produced'],
  ['consumption', 'Consumed'], ['cogs', 'Cost of sales'], ['out_adjust', 'Issue adj.'],
  ['in_adjust', 'Receipt adj.'],
]

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="mb-6">
      <h2 className="text-sm font-semibold text-neutral-800">{title}</h2>
      {hint && <p className="mb-2 mt-0.5 text-xs text-neutral-500">{hint}</p>}
      {children}
    </section>
  )
}

const th = 'px-2 py-1.5 font-medium'
const td = 'px-2 py-1.5 text-right font-mono tabular-nums'
const neg = (v: number | null | undefined) => (v ?? 0) < -0.005 ? 'text-red-700' : ''

export default function MaterialTracePage() {
  const user = useAuthStore((s) => s.user)
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  // One tab per material: the code is in the path (/finance/analysis/trace/CR0059);
  // ?material= is still read so an old link keeps working.
  const { material: pathMaterial } = useParams()
  const material = (pathMaterial ?? params.get('material') ?? '').trim().toUpperCase()
  const from = params.get('from') ?? `${thisMonth().slice(0, 4)}-01`
  const to = params.get('to') ?? thisMonth()

  const [draft, setDraft] = useState(material)
  const [dFrom, setDFrom] = useState(from)
  const [dTo, setDTo] = useState(to)
  const [showLines, setShowLines] = useState(false)
  useEffect(() => { setDraft(material); setDFrom(from); setDTo(to) }, [material, from, to])

  const { data: picks } = useQuery({
    queryKey: ['cost-materials', draft],
    queryFn: () => financeApi.get<{ materials: { code: string; name: string | null }[] }>(
      `/cost-analysis/materials?limit=30&q=${encodeURIComponent(draft)}`),
    enabled: draft.length >= 2,
    staleTime: 60_000,
  })

  const { data, error, isFetching } = useQuery({
    queryKey: ['cost-trace', material, from, to],
    queryFn: () => financeApi.get<Trace>(
      `/cost-analysis/trace?material=${encodeURIComponent(material)}&from=${from}&to=${to}`),
    enabled: !!material && from <= to,
  })

  // The trace's sub-lists are each returned whole; rows carry no state, so
  // each sorts on the page. Signed values sort as signed (an issue line shows
  // its quantity and amount with a minus).
  const setl = useSortableRows(data?.settlements, {
    batch: (s) => s.batch_code,
    provisional: (s) => (s.provisional ? `${s.provisional.bill_code ?? ''} (${s.provisional.period})` : null),
    qty: (s) => s.settled.qty, prov_unit: (s) => s.provisional?.unit,
    settlement: (s) => `${s.settlement_code} (${s.settlement_period})`,
    settled_unit: (s) => s.settled.unit, variance: (s) => s.variance,
  })
  const adj = useSortableRows(data?.out_adjustments, {
    month: (a) => a.period, adjustment: (a) => a.bill_code, amount: (a) => a.amount,
    attached: (a) => a.attached_to, mo: (a) => a.mo_code,
    landed: (a) => a.landed_on.map((l) => `${l.material_code} ${l.material_name ?? ''}`).join('; '),
  })
  const vch = useSortableRows(data?.vouchers, {
    month: (v) => v.period, document: (v) => v.bill_code, what: (v) => v.document,
    amount: (v) => v.document_amount, voucher: (v) => v.voucher?.voucher_no,
    voucher_month: (v) => v.voucher?.period,
    status: (v) => (v.voucher ? v.voucher.status_label : 'No voucher yet'),
    narration: (v) => v.voucher?.explanation,
  })
  const cons = useSortableRows(data?.consumers, {
    month: (c) => c.period, object: (c) => c.cost_object_code || c.cost_object_name, kind: (c) => c.kind,
    qty: (c) => c.qty || null, value: (c) => c.amount, unit: (c) => c.unit,
  })
  const lines = useSortableRows(data?.lines, {
    month: (l) => l.period, date: (l) => l.biz_date, document: (l) => l.bill_code,
    what: (l) => l.category_label, trantype: (l) => l.trantype,
    qty: (l) => (l.qty == null ? null : l.direction === 'out' ? -l.qty : l.qty),
    price: (l) => l.price, amount: (l) => (l.direction === 'out' ? -l.amount : l.amount),
    batch: (l) => l.batch_code, order: (l) => l.mo_code, source: (l) => l.src_code,
  })

  if (!user) return <Navigate to="/login" replace />

  const apply = () => {
    const code = draft.trim().toUpperCase()
    if (!code || dFrom > dTo) return
    if (code === material && pathMaterial) {
      // Same material, new range: stay in this tab.
      setParams({ from: dFrom, to: dTo })
    } else {
      // Another material opens (or focuses) its own tab.
      navigate(`/finance/analysis/trace/${encodeURIComponent(code)}?from=${dFrom}&to=${dTo}`)
    }
  }

  // Other receipts minus other issues (stock counts, scrap, samples, …).
  const other = (m: Month) => (m.flows.other_in?.amount ?? 0) - (m.flows.other_out?.amount ?? 0)

  return (
    <PortalChromeLayout activeKey="portal:/finance/analysis/trace" title="Material Cost Trace"
                        subtitle="AI Financial Analysis · one material, month by month, from NC's own ledger">
      <div className="mx-auto max-w-7xl">
        <SyncBar />

        <form className="mb-5 flex flex-wrap items-center gap-2 text-sm"
              onSubmit={(e) => { e.preventDefault(); apply() }}>
          <input list="cost-material-picks" value={draft} onChange={(e) => setDraft(e.target.value)}
                 placeholder="Material code, e.g. CR0059"
                 className="w-56 rounded-lg border border-neutral-300 px-2.5 py-1 font-mono" />
          <datalist id="cost-material-picks">
            {(picks?.materials ?? []).map((m) => <option key={m.code} value={m.code}>{m.name}</option>)}
          </datalist>
          <label className="text-xs text-neutral-500">From</label>
          <input type="month" value={dFrom} onChange={(e) => setDFrom(e.target.value)}
                 className="rounded-lg border border-neutral-300 px-2 py-1" />
          <label className="text-xs text-neutral-500">To</label>
          <input type="month" value={dTo} onChange={(e) => setDTo(e.target.value)}
                 className="rounded-lg border border-neutral-300 px-2 py-1" />
          <button type="submit" disabled={!draft.trim() || dFrom > dTo}
                  className="rounded-lg bg-[#085E5E] px-3 py-1 text-white disabled:opacity-40">
            Trace
          </button>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />}
        </form>

        {!material ? (
          <div className="rounded-lg border border-neutral-200 px-4 py-10 text-center text-sm text-neutral-500">
            Pick a material to rebuild its cost month by month.
          </div>
        ) : error ? <AccessMessage error={error} /> : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <>
            <p className="mb-4 text-sm">
              <span className="font-mono font-semibold">{data.material.code}</span>{' '}
              <span className="text-neutral-600">{data.material.name}</span>
            </p>

            {data.diagnosis && <div className="mb-6"><DiagnosisPanel d={data.diagnosis} /></div>}

            <h2 className="mb-3 border-b border-neutral-200 pb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">
              Supporting data
            </h2>

            <Section title="Month by month"
                     hint="Opening + receipts − issues = closing, rebuilt from every NC ledger line. Closed months are held against NC's month-end balance. Transfers between warehouses (incl. production picking) are shown net — they move stock, they do not consume it.">
              <div className="overflow-x-auto rounded-lg border border-neutral-200">
                <table className="w-full text-xs">
                  <thead className="bg-neutral-50 text-left text-neutral-500">
                    <tr>
                      <th className={th}>Month</th>
                      <th className={cn(th, 'text-right')}>Opening</th>
                      {FLOW_COLS.map(([k, label]) => <th key={k} className={cn(th, 'text-right')}>{label}</th>)}
                      <th className={cn(th, 'text-right')}>Transfers (net)</th>
                      <th className={cn(th, 'text-right')}>Other</th>
                      <th className={cn(th, 'text-right')}>Closing qty</th>
                      <th className={cn(th, 'text-right')}>Closing value</th>
                      <th className={cn(th, 'text-right')}>Unit on hand</th>
                      <th className={cn(th, 'text-right')}>Produced unit cost</th>
                      <th className={th}>NC balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.months.map((m) => {
                      const tIn = m.flows.transfer_in?.amount ?? 0
                      const tOut = m.flows.transfer_out?.amount ?? 0
                      return (
                        <tr key={m.period} className="border-t border-neutral-100">
                          <td className="whitespace-nowrap px-2 py-1.5 font-mono">
                            {m.period}{!m.period_closed && <OpenPeriodBadge />}
                          </td>
                          <td className={cn(td, neg(m.opening.amount))}>{num(m.opening.amount)}</td>
                          {FLOW_COLS.map(([k]) => {
                            const f = m.flows[k]
                            return (
                              <td key={k} className={cn(td, neg(f?.amount))}
                                  title={f ? `qty ${qty(f.qty)}` : ''}>
                                {f ? num(f.amount) : ''}
                              </td>
                            )
                          })}
                          <td className={td} title={`in ${num(tIn)} · out ${num(tOut)}`}>
                            {m.flows.transfer_in || m.flows.transfer_out ? num(tIn - tOut) : ''}
                          </td>
                          <td className={td}>{other(m) ? num(other(m)) : ''}</td>
                          <td className={td}>{qty(m.closing.qty)}</td>
                          <td className={cn(td, neg(m.closing.amount), 'font-semibold')}>{num(m.closing.amount)}</td>
                          <td className={cn(td, neg(m.closing.unit))}>{num(m.closing.unit, 4)}</td>
                          <td className={cn(td, neg(m.production_unit_cost), 'font-semibold')}>{num(m.production_unit_cost, 4)}</td>
                          <td className="whitespace-nowrap px-2 py-1.5">
                            {m.balance_matches === null ? <span className="text-neutral-400">not closed</span>
                              : m.balance_matches ? <span className="text-[#085E5E]">matches</span>
                              : <span className="font-medium text-red-700">differs ({num(m.nc_balance?.amount ?? 0)})</span>}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-1 text-[11px] text-neutral-400">Issue columns are the value that left; a negative issue adjustment means NC took value off a consumption after the fact.</p>
            </Section>

            {data.product_cost.length > 0 && (
              <Section title="Product cost in NC, split by where each input's value came from"
                       hint="Consumed = issues this cost object took in the month. Settlement adjustment = amount-only adjustments (a provisional price settled differently, after the stock was used). Carried in = cost collected in an earlier month with no output, rolled into this one.">
                {data.product_cost.map((p) => (
                  <div key={p.period} className="mb-3 overflow-x-auto rounded-lg border border-neutral-200">
                    <div className="flex flex-wrap gap-4 bg-neutral-50 px-3 py-2 text-xs">
                      <span className="font-mono font-semibold">{p.period}</span>
                      <span>Output {qty(p.qty)}</span>
                      <span className={neg(p.amount)}>Cost {num(p.amount)}</span>
                      <span className={cn('font-semibold', neg(p.unit))}>Unit {num(p.unit, 4)}</span>
                      {Math.abs(p.adjustment_total) >= 0.01 && (
                        <span className={cn('font-medium', neg(p.adjustment_total))}>
                          of which settlement adjustments {num(p.adjustment_total)}
                        </span>
                      )}
                    </div>
                    <table className="w-full text-xs">
                      <thead className="text-left text-neutral-500"><tr>
                        <th className={th}>Input</th><th className={th}>Element</th>
                        <th className={cn(th, 'text-right')}>Consumed qty</th>
                        <th className={cn(th, 'text-right')}>Consumed</th>
                        <th className={cn(th, 'text-right')}>Settlement adj.</th>
                        <th className={cn(th, 'text-right')}>Carried in</th>
                        <th className={cn(th, 'text-right')}>NC total</th>
                      </tr></thead>
                      <tbody>
                        {p.inputs.map((i, n) => (
                          <tr key={n} className="border-t border-neutral-100">
                            <td className="px-2 py-1.5"><span className="font-mono">{i.material_code ?? '—'}</span> <span className="text-neutral-500">{i.material_name}</span></td>
                            <td className="px-2 py-1.5 text-neutral-500">{i.element_name}</td>
                            <td className={td}>{qty(i.consumed_qty)}</td>
                            <td className={cn(td, neg(i.consumed_amount))}>{num(i.consumed_amount)}</td>
                            <td className={cn(td, neg(i.adjustment), Math.abs(i.adjustment) >= 0.01 && 'font-semibold')}>{Math.abs(i.adjustment) >= 0.01 ? num(i.adjustment) : ''}</td>
                            <td className={td}>{i.carried_in != null && Math.abs(i.carried_in) >= 0.01 ? num(i.carried_in) : ''}</td>
                            <td className={cn(td, neg(i.nc_amount))}>{num(i.nc_amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </Section>
            )}

            {data.settlements.length > 0 && (
              <Section title="Provisional receipts and their settlement"
                       hint="A receipt entered at a provisional price is reversed and re-entered at the settled price. If the stock was already used, the variance cannot stay in inventory and NC pushes it onto a later consumption.">
                <div className="overflow-x-auto rounded-lg border border-neutral-200">
                  <table className="w-full text-xs">
                    <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
                      <SortTh label="Batch" sortKey="batch" sort={setl.sort} onSort={setl.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Provisional receipt" sortKey="provisional" sort={setl.sort} onSort={setl.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Qty" sortKey="qty" sort={setl.sort} onSort={setl.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Provisional unit" sortKey="prov_unit" sort={setl.sort} onSort={setl.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Settlement" sortKey="settlement" sort={setl.sort} onSort={setl.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Settled unit" sortKey="settled_unit" sort={setl.sort} onSort={setl.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Variance" sortKey="variance" sort={setl.sort} onSort={setl.onSort} align="right" className="px-2 py-1.5 font-medium" />
                    </tr></thead>
                    <tbody>
                      {setl.rows.map((s, n) => (
                        <tr key={n} className="border-t border-neutral-100">
                          <td className="px-2 py-1.5 font-mono">{s.batch_code ?? '—'}</td>
                          <td className="px-2 py-1.5 font-mono">
                            {s.provisional ? `${s.provisional.bill_code ?? ''} (${s.provisional.period})` : <span className="text-amber-700">not found</span>}
                          </td>
                          <td className={td}>{qty(s.settled.qty)}</td>
                          <td className={td}>{num(s.provisional?.unit, 4)}</td>
                          <td className="px-2 py-1.5 font-mono">{s.settlement_code} ({s.settlement_period})</td>
                          <td className={td}>{num(s.settled.unit, 4)}</td>
                          <td className={cn(td, neg(s.variance), 'font-semibold')}>{num(s.variance)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            )}

            {data.out_adjustments.length > 0 && (
              <Section title="Issue adjustments (IA) and where they landed"
                       hint="Value NC moved onto a consumption after the fact — settlement variances, and the month-end tail when quantity reaches zero but value does not.">
                <div className="overflow-x-auto rounded-lg border border-neutral-200">
                  <table className="w-full text-xs">
                    <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
                      <SortTh label="Month" sortKey="month" sort={adj.sort} onSort={adj.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Adjustment" sortKey="adjustment" sort={adj.sort} onSort={adj.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Amount" sortKey="amount" sort={adj.sort} onSort={adj.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Attached to" sortKey="attached" sort={adj.sort} onSort={adj.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Production order" sortKey="mo" sort={adj.sort} onSort={adj.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Landed on" sortKey="landed" sort={adj.sort} onSort={adj.onSort} className="px-2 py-1.5 font-medium" />
                    </tr></thead>
                    <tbody>
                      {adj.rows.map((a, n) => (
                        <tr key={n} className="border-t border-neutral-100">
                          <td className="px-2 py-1.5 font-mono">{a.period}</td>
                          <td className="px-2 py-1.5 font-mono">{a.bill_code}</td>
                          <td className={cn(td, neg(a.amount), Math.abs(a.amount) >= 1000 && 'font-semibold')}>{num(a.amount)}</td>
                          <td className="px-2 py-1.5 font-mono">{a.attached_to ?? '—'}</td>
                          <td className="px-2 py-1.5 font-mono">{a.mo_code ?? '—'}</td>
                          <td className="px-2 py-1.5">{a.landed_on.map((l) => `${l.material_code} ${l.material_name ?? ''}`).join('; ') || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            )}

            {data.vouchers.length > 0 && (
              <Section title={`Vouchers behind the adjustments — unposted included${data.vouchers_unposted ? ` (${data.vouchers_unposted} unposted)` : ''}`}
                       hint="Every GL voucher NC generated for this material's adjustments and for the issues they sit on, whatever its status. An unposted voucher is not in NC's ledger yet, so a posted-only report would not show it.">
                <div className="overflow-x-auto rounded-lg border border-neutral-200">
                  <table className="w-full text-xs">
                    <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
                      <SortTh label="Month" sortKey="month" sort={vch.sort} onSort={vch.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Document" sortKey="document" sort={vch.sort} onSort={vch.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="What" sortKey="what" sort={vch.sort} onSort={vch.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Amount" sortKey="amount" sort={vch.sort} onSort={vch.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Voucher" sortKey="voucher" sort={vch.sort} onSort={vch.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Voucher month" sortKey="voucher_month" sort={vch.sort} onSort={vch.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Status" sortKey="status" sort={vch.sort} onSort={vch.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Narration" sortKey="narration" sort={vch.sort} onSort={vch.onSort} className="px-2 py-1.5 font-medium" />
                    </tr></thead>
                    <tbody>
                      {vch.rows.map((v, n) => (
                        <tr key={n} className="border-t border-neutral-100">
                          <td className="px-2 py-1.5 font-mono">{v.period}</td>
                          <td className="px-2 py-1.5 font-mono">{v.bill_code}</td>
                          <td className="px-2 py-1.5">{v.document}</td>
                          <td className={cn(td, neg(v.document_amount))}>{num(v.document_amount)}</td>
                          <td className="px-2 py-1.5 font-mono">{v.voucher?.voucher_no ?? '—'}</td>
                          <td className="px-2 py-1.5 font-mono">{v.voucher?.period ?? ''}</td>
                          <td className="px-2 py-1.5">
                            {!v.voucher ? <span className="text-neutral-500">No voucher yet</span>
                              : <span className={cn('inline-block rounded border px-1.5 py-0.5 text-[11px] font-medium',
                                  v.voucher.status === 'posted' ? 'border-neutral-200 bg-neutral-50 text-neutral-600'
                                  : v.voucher.status === 'unposted' ? 'border-amber-300 bg-amber-50 text-amber-800'
                                  : 'border-red-200 bg-red-50 text-red-700')}>{v.voucher.status_label}</span>}
                          </td>
                          <td className="px-2 py-1.5 text-neutral-500">{v.voucher?.explanation ?? ''}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            )}

            {data.consumers.length > 0 && (
              <Section title="Who consumed it" hint="From NC's cost collection, by cost object. Adjustment rows are amount-only value moved onto that cost object.">
                <div className="overflow-x-auto rounded-lg border border-neutral-200">
                  <table className="w-full text-xs">
                    <thead className="bg-neutral-50 text-left text-neutral-500"><tr>
                      <SortTh label="Month" sortKey="month" sort={cons.sort} onSort={cons.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Cost object" sortKey="object" sort={cons.sort} onSort={cons.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Kind" sortKey="kind" sort={cons.sort} onSort={cons.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Qty" sortKey="qty" sort={cons.sort} onSort={cons.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Value" sortKey="value" sort={cons.sort} onSort={cons.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Unit" sortKey="unit" sort={cons.sort} onSort={cons.onSort} align="right" className="px-2 py-1.5 font-medium" />
                    </tr></thead>
                    <tbody>
                      {cons.rows.map((c, n) => (
                        <tr key={n} className="border-t border-neutral-100">
                          <td className="px-2 py-1.5 font-mono">{c.period}</td>
                          <td className="px-2 py-1.5"><span className="font-mono">{c.cost_object_code}</span> <span className="text-neutral-500">{c.cost_object_name}</span></td>
                          <td className="px-2 py-1.5">{c.kind === 'adjustment' ? <span className="text-amber-800">adjustment</span> : 'consumption'}</td>
                          <td className={td}>{c.qty ? qty(c.qty) : ''}</td>
                          <td className={cn(td, neg(c.amount))}>{num(c.amount)}</td>
                          <td className={cn(td, neg(c.unit))}>{num(c.unit, 4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            )}

            <Section title={`NC ledger lines (${data.lines.length})`}>
              <button onClick={() => setShowLines(!showLines)}
                      className="mb-2 inline-flex items-center gap-1 text-xs text-[#085E5E]">
                {showLines ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                {showLines ? 'Hide' : 'Show'} every line
              </button>
              {showLines && (
                <div className="max-h-[480px] overflow-auto rounded-lg border border-neutral-200">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-neutral-50 text-left text-neutral-500"><tr>
                      <SortTh label="Month" sortKey="month" sort={lines.sort} onSort={lines.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Date" sortKey="date" sort={lines.sort} onSort={lines.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Document" sortKey="document" sort={lines.sort} onSort={lines.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="What" sortKey="what" sort={lines.sort} onSort={lines.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Trade type" sortKey="trantype" sort={lines.sort} onSort={lines.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Qty" sortKey="qty" sort={lines.sort} onSort={lines.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Price" sortKey="price" sort={lines.sort} onSort={lines.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Amount" sortKey="amount" sort={lines.sort} onSort={lines.onSort} align="right" className="px-2 py-1.5 font-medium" />
                      <SortTh label="Batch" sortKey="batch" sort={lines.sort} onSort={lines.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Order" sortKey="order" sort={lines.sort} onSort={lines.onSort} className="px-2 py-1.5 font-medium" />
                      <SortTh label="Source" sortKey="source" sort={lines.sort} onSort={lines.onSort} className="px-2 py-1.5 font-medium" />
                    </tr></thead>
                    <tbody>
                      {lines.rows.map((l, n) => (
                        <Fragment key={n}>
                          <tr className="border-t border-neutral-100">
                            <td className="px-2 py-1 font-mono">{l.period}</td>
                            <td className="px-2 py-1 font-mono">{l.biz_date ?? '—'}</td>
                            <td className="px-2 py-1 font-mono">{l.bill_code}</td>
                            <td className="px-2 py-1">{l.category_label}{l.estimate && <span className="ml-1 text-amber-700">(provisional)</span>}</td>
                            <td className="px-2 py-1 text-neutral-500">{l.trantype}</td>
                            <td className={td}>{l.qty != null ? `${l.direction === 'out' ? '−' : ''}${qty(l.qty)}` : ''}</td>
                            <td className={td}>{num(l.price, 4)}</td>
                            <td className={cn(td, neg(l.amount))}>{l.direction === 'out' ? '−' : ''}{num(l.amount)}</td>
                            <td className="px-2 py-1 font-mono">{l.batch_code}</td>
                            <td className="px-2 py-1 font-mono">{l.mo_code}</td>
                            <td className="px-2 py-1 font-mono">{l.src_code}</td>
                          </tr>
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}
