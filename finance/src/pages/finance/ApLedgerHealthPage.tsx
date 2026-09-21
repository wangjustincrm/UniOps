/**
 * AP Subledger Health — which supplier balances in NC can be believed.
 *
 * NC's payable subledger reports far more open than billed-minus-paid does.
 * The clearest case: 2021 milk was billed 32,641,249.87, paid 32,114,350.99,
 * and 13,411,519.10 of those lines are still flagged open. The money moved; the
 * payment was never applied against the lines.
 *
 * This page does NOT decide who is right. It cannot: billed-minus-paid is not a
 * truth either — a supplier paid beyond what was billed is holding a prepayment
 * or an unapplied credit, which is normal. So the page reports that NC's own two
 * figures disagree, by how much, and then hands over the documents behind the
 * gap so finance can judge each supplier.
 *
 * Currencies are never summed together. This company transacts in CAD, USD, CNY
 * and EUR, and one combined figure would mean nothing.
 */
import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Download, Loader2, X } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

interface Side { suppliers: number; subledger_open: string; billed_minus_paid: string; gap: string }
interface CcyRow { currency: string; consistent: Side; uncleared: Side }
interface SummaryResp { currencies: CcyRow[] }

interface SupplierRow {
  supplier_code: string | null
  supplier_name: string | null
  currency: string
  health: string
  billed: string
  paid: string
  subledger_open: string
  billed_minus_paid: string
  gap: string
}
interface ItemsResp { total: number; items: SupplierRow[] }

interface OpenBill {
  bill_no: string; bill_date: string | null; bill_year: string | null
  money_cr: string | null; money_bal: string | null
  invoice_no: string | null; purchase_order: string | null
  trade_type: string | null; src_syscode: number | null; scomment: string | null
}
interface PayLine {
  bill_no: string; pay_date: string | null; bill_year: string | null
  money_de: string | null; src_bill_type: string | null; src_bill_id: string | null
  scomment: string | null
}
interface DetailResp { supplier_code: string; currency: string; open_bills: OpenBill[]; payments: PayLine[] }

function money(v: string | null | undefined) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function csvEscape(v: unknown) {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
/** Already YYYY-MM-DD from the API — never re-parse a date-only string. */
function day(v: string | null) { return v ? v.slice(0, 10) : '—' }

function SupplierDetail({ code, name, currency, onClose }: {
  code: string; name: string | null; currency: string; onClose: () => void
}) {
  const { data, isFetching } = useQuery({
    queryKey: ['ap-ledger-supplier', code, currency],
    queryFn: () => financeApi.get<DetailResp>(
      `/ap-ledger-health/supplier?supplier_code=${encodeURIComponent(code)}&currency=${currency}`),
  })
  const bills = data?.open_bills ?? []
  const pays = data?.payments ?? []
  const untied = pays.filter((p) => !p.src_bill_id).length

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-6">
      <div className="w-full max-w-6xl rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between border-b border-neutral-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-neutral-900">{name ?? code}</h2>
            <p className="mt-0.5 text-xs text-neutral-500">
              {code} · {currency} · {bills.length} bills still flagged open · {pays.length} payment lines
              {untied > 0 && (
                <> · <span className="text-amber-700">{untied} payment lines tied to no document</span></>
              )}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close"
                  className="rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        {isFetching && !data ? (
          <div className="py-16 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="grid gap-5 p-5 lg:grid-cols-2">
            <div className="min-w-0">
              <h3 className="mb-2 text-sm font-semibold text-neutral-800">Bills NC still shows as open</h3>
              <div className="max-h-[52vh] overflow-auto rounded-lg border border-neutral-200">
                <table className="w-full min-w-[440px] text-xs">
                  <thead className="sticky top-0 bg-neutral-50">
                    <tr className="border-b border-neutral-100 text-[11px] text-neutral-500">
                      <th className="px-2.5 py-2 text-left font-medium">Bill</th>
                      <th className="px-2.5 py-2 text-left font-medium">Date</th>
                      <th className="px-2.5 py-2 text-left font-medium">Invoice</th>
                      <th className="px-2.5 py-2 text-right font-medium">Billed</th>
                      <th className="px-2.5 py-2 text-right font-medium">Still open</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bills.length === 0 ? (
                      <tr><td colSpan={5} className="px-2.5 py-6 text-center text-neutral-400">None.</td></tr>
                    ) : bills.map((b) => (
                      <tr key={b.bill_no + b.invoice_no} className="border-t border-neutral-100">
                        <td className="px-2.5 py-1.5 font-mono">{b.bill_no}</td>
                        <td className="px-2.5 py-1.5 text-neutral-600">{day(b.bill_date)}</td>
                        <td className="px-2.5 py-1.5 font-mono text-neutral-600">{b.invoice_no ?? '—'}</td>
                        <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{money(b.money_cr)}</td>
                        <td className="px-2.5 py-1.5 text-right font-mono tabular-nums font-semibold">{money(b.money_bal)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="min-w-0">
              <h3 className="mb-2 text-sm font-semibold text-neutral-800">Payments recorded for this supplier</h3>
              <div className="max-h-[52vh] overflow-auto rounded-lg border border-neutral-200">
                <table className="w-full min-w-[440px] text-xs">
                  <thead className="sticky top-0 bg-neutral-50">
                    <tr className="border-b border-neutral-100 text-[11px] text-neutral-500">
                      <th className="px-2.5 py-2 text-left font-medium">Payment</th>
                      <th className="px-2.5 py-2 text-left font-medium">Paid on</th>
                      <th className="px-2.5 py-2 text-right font-medium">Amount</th>
                      <th className="px-2.5 py-2 text-left font-medium">Applied to</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pays.length === 0 ? (
                      <tr><td colSpan={4} className="px-2.5 py-6 text-center text-neutral-400">None.</td></tr>
                    ) : pays.map((p, i) => (
                      <tr key={p.bill_no + i} className="border-t border-neutral-100">
                        <td className="px-2.5 py-1.5 font-mono">{p.bill_no}</td>
                        <td className="px-2.5 py-1.5 text-neutral-600">{day(p.pay_date)}</td>
                        <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{money(p.money_de)}</td>
                        {/* Empty here is the story: a payment tied to no source
                            document is exactly how a subledger stops clearing. */}
                        <td className={cn('px-2.5 py-1.5 font-mono',
                                          p.src_bill_id ? 'text-neutral-600' : 'text-amber-700')}>
                          {p.src_bill_id ? (p.src_bill_type ?? 'linked') : 'nothing'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function ApLedgerHealthPage() {
  const { user } = useAuthStore()
  const [currency, setCurrency] = useState('CAD')
  const [health, setHealth] = useState<string>('uncleared')
  const [open, setOpen] = useState<{ code: string; name: string | null } | null>(null)

  const { data: summary, isFetching: loadingSummary } = useQuery({
    queryKey: ['ap-ledger-health-summary'],
    queryFn: () => financeApi.get<SummaryResp>('/ap-ledger-health/summary'),
  })
  const { data: items, isFetching: loadingItems } = useQuery({
    queryKey: ['ap-ledger-health-items', currency, health],
    queryFn: () => financeApi.get<ItemsResp>(
      `/ap-ledger-health/items?currency=${currency}&health=${health}&limit=500`),
  })

  const ccyRows = summary?.currencies ?? []

  const exportCsv = () => {
    const head = ['Supplier code', 'Supplier', 'Currency', 'Health', 'Billed', 'Paid',
                  'Subledger open', 'Billed minus paid', 'Gap']
    const body = (items?.items ?? []).map((r) => [
      r.supplier_code, r.supplier_name, r.currency, r.health,
      r.billed, r.paid, r.subledger_open, r.billed_minus_paid, r.gap])
    const csv = [head, ...body].map((row) => row.map(csvEscape).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `ap-subledger-health-${currency}-${health}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/ap-ledger-health"
      title="AP Subledger Health"
      subtitle="Where NC's open balance and its own billed-minus-paid disagree"
    >
      <div className="mx-auto max-w-7xl">
        <div className="mb-5 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm leading-relaxed text-neutral-700">
          <p className="mb-1.5">
            NC holds two figures for what a supplier is owed: the <strong>open balance on the
            payable lines</strong>, and <strong>what was billed minus what was paid</strong>. For most
            suppliers they agree exactly. Where they do not, the open balance is carrying
            payables that were paid but never applied against the lines.
          </p>
          <p className="text-neutral-500">
            This page does not decide which figure is right — paying beyond what was billed is
            normal too (a prepayment, an unapplied credit). It shows that the two disagree and
            hands over the documents behind the gap. <strong>The judgement is finance&rsquo;s, per supplier.</strong>
          </p>
        </div>

        {loadingSummary && !summary ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {ccyRows.map((c) => {
              const bad = Number(c.uncleared.gap) !== 0
              return (
                <button key={c.currency} onClick={() => setCurrency(c.currency)}
                        className={cn('rounded-lg border p-3 text-left transition-colors',
                                      currency === c.currency
                                        ? 'border-[#085E5E] bg-primary-50/60'
                                        : 'border-neutral-200 hover:bg-neutral-50')}>
                  <div className="flex items-center gap-1.5">
                    {bad ? <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                         : <CheckCircle2 className="h-3.5 w-3.5 text-[#085E5E]" />}
                    <span className="font-mono text-sm font-semibold">{c.currency}</span>
                  </div>
                  <dl className="mt-2 space-y-1 text-xs">
                    <div className="flex justify-between gap-2">
                      <dt className="text-neutral-500">Trustworthy</dt>
                      <dd className="font-mono tabular-nums">{money(c.consistent.subledger_open)}</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-neutral-500">In question</dt>
                      <dd className="font-mono tabular-nums">{money(c.uncleared.subledger_open)}</dd>
                    </div>
                    <div className="flex justify-between gap-2 border-t border-neutral-200 pt-1">
                      <dt className="font-medium text-amber-700">Overstated by</dt>
                      <dd className="font-mono tabular-nums font-semibold text-amber-700">{money(c.uncleared.gap)}</dd>
                    </div>
                  </dl>
                  <p className="mt-1.5 text-[11px] text-neutral-400">
                    {c.consistent.suppliers} agree · {c.uncleared.suppliers} disagree
                  </p>
                </button>
              )
            })}
          </div>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="inline-flex overflow-hidden rounded-lg border border-neutral-300">
            {[['uncleared', 'Disagree'], ['consistent', 'Agree']].map(([k, label]) => (
              <button key={k} onClick={() => setHealth(k)}
                      className={cn('px-3 py-1.5 text-sm',
                                    health === k ? 'bg-[#085E5E] text-white' : 'bg-white text-neutral-700 hover:bg-neutral-50')}>
                {label}
              </button>
            ))}
          </div>
          <span className="text-sm text-neutral-500">
            {currency} · {items?.total ?? 0} suppliers
          </span>
          <button onClick={exportCsv}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50">
            <Download className="h-4 w-4" /> Export CSV
          </button>
        </div>

        <div className="overflow-x-auto rounded-lg border border-neutral-200">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                <th className="px-3 py-2 text-left font-medium">Supplier</th>
                <th className="px-3 py-2 text-right font-medium">Billed</th>
                <th className="px-3 py-2 text-right font-medium">Paid</th>
                <th className="px-3 py-2 text-right font-medium">Subledger open</th>
                <th className="px-3 py-2 text-right font-medium">Billed &minus; paid</th>
                <th className="px-3 py-2 text-right font-medium">Gap</th>
                <th className="px-3 py-2 text-left font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {loadingItems && !items ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></td></tr>
              ) : (items?.items.length ?? 0) === 0 ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-neutral-400">
                  No suppliers in this state for {currency}.</td></tr>
              ) : items!.items.map((r) => (
                <tr key={(r.supplier_code ?? '') + r.currency} className="border-t border-neutral-100 hover:bg-neutral-50/60">
                  <td className="px-3 py-2">
                    <div className="text-neutral-800">{r.supplier_name ?? '—'}</div>
                    <div className="font-mono text-[11px] text-neutral-400">{r.supplier_code ?? '—'}</div>
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-neutral-600">{money(r.billed)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-neutral-600">{money(r.paid)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{money(r.subledger_open)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{money(r.billed_minus_paid)}</td>
                  <td className={cn('px-3 py-2 text-right font-mono tabular-nums font-semibold',
                                    Number(r.gap) === 0 ? 'text-neutral-400' : 'text-amber-700')}>
                    {money(r.gap)}
                  </td>
                  <td className="px-3 py-2">
                    {r.supplier_code && (
                      <button onClick={() => setOpen({ code: r.supplier_code!, name: r.supplier_name })}
                              className="text-xs font-medium text-[#085E5E] hover:underline">
                        Show documents
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {open && (
        <SupplierDetail code={open.code} name={open.name} currency={currency}
                        onClose={() => setOpen(null)} />
      )}
    </PortalChromeLayout>
  )
}
