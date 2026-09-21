/**
 * AP Cash Flow — what we have to pay, and when.
 *
 * The question finance has been asking all along: which payables are due, and
 * how much cash the next quarter needs. Built on NC's approved open balance,
 * because NC is the book of record for payable balances.
 *
 * The page is shaped by three things the data does, each of which would make a
 * naive forecast wrong:
 *
 *   Two fifths of the open balance has NO payment term — suppliers NC bills
 *   that have no vendor record here at all. They are not defaulted to net30 and
 *   folded into a bucket; they are stated as undated, with the supplier list,
 *   because inventing a due date for that much money is worse than admitting
 *   the hole. The list is a work queue: each supplier is one vendor record or
 *   one term setting away from being forecastable.
 *
 *   Most of the balance is already long overdue — the stale tail NC cannot
 *   clear. So overdue is aged into bands and kept OUT of the forward buckets,
 *   and the headline "cash needed" figure is the forward side only.
 *
 *   Bills finance has set aside on AP Subledger Health never appear here.
 *
 * Currencies are never summed together.
 */
import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Download, HelpCircle, Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

interface Bucket {
  key: string; label: string; overdue: boolean
  bills: number; amount: string
  first_due: string | null; last_due: string | null
}
interface CcyRow {
  currency: string
  buckets: Bucket[]
  total: string
  overdue: string
  undated: string
  undated_bills: number
  /** Dated and not yet due — the only part of this that is a forecast. */
  forward: string
}
interface SummaryResp {
  currencies: CcyRow[]
  as_of: string
  mirror: { synced_at: string | null; tie_out_ok: boolean | null }
}

interface BillRow {
  bill_no: string; currency: string; bill_date: string | null
  supplier_code: string | null; supplier_name: string | null
  invoice_no: string | null; purchase_order: string | null; trade_type: string | null
  open_bal: string
  payment_terms: string | null; term_days: number | null
  due_date: string | null; days_to_due: number | null
  bucket: string; bucket_label: string
  no_vendor_record: boolean
}
interface ItemsResp { total: number; amount: string; items: BillRow[] }

interface MissingRow {
  supplier_code: string | null; supplier_name: string | null; currency: string
  reason: 'no_vendor_record' | 'unrecognised_term'
  payment_terms: string | null
  bills: number; amount: string
  oldest_bill: string | null; newest_bill: string | null
}
interface MissingResp { total: number; items: MissingRow[] }

const UNDATED = 'undated'

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
/** Already YYYY-MM-DD from the API — a date-only string must never go through
 *  new Date(), which renders yesterday in a UTC-behind zone. */
function day(v: string | null) { return v ? v.slice(0, 10) : '—' }

const cell = 'px-3 py-2 text-right font-mono tabular-nums'

export default function ApCashFlowPage() {
  const { user } = useAuthStore()
  const [currency, setCurrency] = useState('CAD')
  const [bucket, setBucket] = useState<string | null>(null)

  const { data: summary, isFetching: loadingSummary } = useQuery({
    queryKey: ['ap-cash-flow-summary'],
    queryFn: () => financeApi.get<SummaryResp>('/ap-cash-flow/summary'),
  })
  const { data: items, isFetching: loadingItems, error: itemsError } = useQuery({
    queryKey: ['ap-cash-flow-items', currency, bucket],
    queryFn: () => financeApi.get<ItemsResp>(
      `/ap-cash-flow/items?currency=${currency}` +
      `${bucket ? `&bucket=${bucket}` : ''}&limit=1000`),
    enabled: !!bucket,
  })
  const { data: missing } = useQuery({
    queryKey: ['ap-cash-flow-missing', currency],
    queryFn: () => financeApi.get<MissingResp>(`/ap-cash-flow/missing-terms?currency=${currency}`),
  })

  const row = summary?.currencies.find((c) => c.currency === currency)
  const overdue = row?.buckets.filter((b) => b.overdue) ?? []
  const forward = row?.buckets.filter((b) => !b.overdue && b.key !== UNDATED) ?? []
  const undated = row?.buckets.find((b) => b.key === UNDATED)

  // What finance actually asked for: cash needed BY a horizon, not just within
  // one band. A per-band figure alone makes everyone add the rows up by hand.
  let running = 0
  const forwardCumulative = forward.map((b) => {
    running += Number(b.amount)
    return { ...b, cumulative: running }
  })

  const exportCsv = async () => {
    const all: BillRow[] = []
    for (let off = 0; ; off += 1000) {
      const page = await financeApi.get<ItemsResp>(
        `/ap-cash-flow/items?currency=${currency}&limit=1000&offset=${off}`)
      all.push(...page.items)
      if (all.length >= page.total || page.items.length === 0) break
    }
    const head = ['Bill', 'Supplier code', 'Supplier', 'Invoice no', 'PO', 'Currency',
                  'Bill date', 'Payment term', 'Term days', 'Due date', 'Days to due',
                  'Open balance', 'Bucket', 'No vendor record']
    const body = all.map((r) => [
      r.bill_no, r.supplier_code, r.supplier_name, r.invoice_no, r.purchase_order,
      r.currency, r.bill_date, r.payment_terms, r.term_days, r.due_date, r.days_to_due,
      r.open_bal, r.bucket_label, r.no_vendor_record ? 'yes' : 'no'])
    const csv = [head, ...body].map((c) => c.map(csvEscape).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `ap-cash-flow-${currency}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/ap-cash-flow"
      title="AP Cash Flow"
      subtitle="What is due and when, from NC's approved open balance"
    >
      <div className="mx-auto max-w-7xl">
        {summary?.mirror && (
          <div className={cn(
            'mb-4 flex flex-wrap items-center gap-2 rounded-lg border px-4 py-2.5 text-sm',
            summary.mirror.tie_out_ok === false
              ? 'border-danger-200 bg-danger-50 text-danger-900'
              : 'border-neutral-200 bg-neutral-50 text-neutral-600')}>
            {summary.mirror.tie_out_ok === false
              ? <AlertTriangle className="h-4 w-4 shrink-0" />
              : <CheckCircle2 className="h-4 w-4 shrink-0 text-[#085E5E]" />}
            <span>
              Due dates are calculated as <strong>NC bill date + the supplier&rsquo;s payment term</strong>,
              taken from the vendor master (editable per supplier in EPMS &rsquo;Vendors&rsquo;).
              NC holds no due date of its own. Mirror as of{' '}
              <strong>{summary.mirror.synced_at?.replace('T', ' ').slice(0, 16) ?? 'never'}</strong>
              {summary.mirror.tie_out_ok === false && ' — it does NOT tie out against NC.'}
            </span>
          </div>
        )}

        {loadingSummary && !summary ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(summary?.currencies ?? []).map((c) => (
              <button key={c.currency} onClick={() => { setCurrency(c.currency); setBucket(null) }}
                      className={cn('rounded-lg border p-3 text-left transition-colors',
                                    currency === c.currency
                                      ? 'border-[#085E5E] bg-primary-50/60'
                                      : 'border-neutral-200 hover:bg-neutral-50')}>
                <span className="font-mono text-sm font-semibold">{c.currency}</span>
                <dl className="mt-2 space-y-1 text-xs">
                  {/* The forecast figure comes first because it is the only one
                      that answers "how much cash do we need". */}
                  <div className="flex justify-between gap-2">
                    <dt className="text-neutral-500">Coming due</dt>
                    <dd className="font-mono tabular-nums font-semibold text-[#085E5E]">{money(c.forward)}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-neutral-500">Already overdue</dt>
                    <dd className="font-mono tabular-nums text-amber-700">{money(c.overdue)}</dd>
                  </div>
                  <div className="flex justify-between gap-2 border-t border-neutral-200 pt-1">
                    <dt className="text-neutral-400">No payment term</dt>
                    <dd className="font-mono tabular-nums text-neutral-400">{money(c.undated)}</dd>
                  </div>
                </dl>
                <p className="mt-1.5 text-[11px] text-neutral-400">
                  {money(c.total)} open in total
                </p>
              </button>
            ))}
          </div>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-neutral-500">
            {currency} · as of {summary?.as_of ?? '—'}
          </span>
          {bucket && (
            <button onClick={() => setBucket(null)}
                    className="rounded-lg border border-neutral-300 px-2.5 py-1 text-xs text-neutral-600 hover:bg-neutral-50">
              Clear selection
            </button>
          )}
          <button onClick={exportCsv}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50">
            <Download className="h-4 w-4" /> Export CSV
          </button>
        </div>

        <div className="grid gap-5 lg:grid-cols-2">
          <div className="min-w-0">
            <h3 className="mb-1 text-sm font-semibold text-neutral-800">Coming due</h3>
            <p className="mb-2 text-[11px] leading-relaxed text-neutral-500">
              Cumulative is the answer to &ldquo;how much cash do we need by then&rdquo; — each row
              includes everything above it.
            </p>
            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full min-w-[420px] text-sm">
                <thead>
                  <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                    <th className="px-3 py-2 text-left font-medium">Horizon</th>
                    <th className="px-3 py-2 text-right font-medium">Bills</th>
                    <th className="px-3 py-2 text-right font-medium">Amount</th>
                    <th className="px-3 py-2 text-right font-medium">Cumulative</th>
                  </tr>
                </thead>
                <tbody>
                  {forwardCumulative.every((b) => b.bills === 0) ? (
                    <tr><td colSpan={4} className="px-3 py-6 text-center text-neutral-400">
                      Nothing dated and not yet due in {currency}.
                    </td></tr>
                  ) : forwardCumulative.filter((b) => b.bills > 0).map((b) => (
                    <tr key={b.key}
                        onClick={() => setBucket(bucket === b.key ? null : b.key)}
                        className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-50/60',
                                      bucket === b.key && 'bg-primary-50/60')}>
                      <td className="px-3 py-2">
                        {b.label}
                        {b.first_due && (
                          <div className="text-[11px] text-neutral-400">
                            {day(b.first_due)} – {day(b.last_due)}
                          </div>
                        )}
                      </td>
                      <td className={cell}>{b.bills}</td>
                      <td className={cell}>{money(b.amount)}</td>
                      <td className={cn(cell, 'font-semibold text-[#085E5E]')}>
                        {money(String(b.cumulative))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="min-w-0">
            <h3 className="mb-1 text-sm font-semibold text-neutral-800">Already overdue</h3>
            <p className="mb-2 text-[11px] leading-relaxed text-neutral-500">
              Aged, and kept out of the forecast above. The oldest band is mostly the stale
              tail NC can no longer clear — judge it on{' '}
              <a href="/finance/ap-ledger-health" className="text-[#085E5E] hover:underline">
                AP Subledger Health</a> and anything set aside there stops appearing here.
            </p>
            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full min-w-[420px] text-sm">
                <thead>
                  <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                    <th className="px-3 py-2 text-left font-medium">Age</th>
                    <th className="px-3 py-2 text-right font-medium">Bills</th>
                    <th className="px-3 py-2 text-right font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {overdue.every((b) => b.bills === 0) ? (
                    <tr><td colSpan={3} className="px-3 py-6 text-center text-neutral-400">
                      Nothing overdue in {currency}.
                    </td></tr>
                  ) : overdue.filter((b) => b.bills > 0).map((b) => (
                    <tr key={b.key}
                        onClick={() => setBucket(bucket === b.key ? null : b.key)}
                        className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-50/60',
                                      bucket === b.key && 'bg-primary-50/60')}>
                      <td className="px-3 py-2">
                        {b.label}
                        {b.first_due && (
                          <div className="text-[11px] text-neutral-400">
                            due {day(b.first_due)} – {day(b.last_due)}
                          </div>
                        )}
                      </td>
                      <td className={cell}>{b.bills}</td>
                      <td className={cn(cell, 'font-semibold text-amber-700')}>{money(b.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Declared, never defaulted. This is money the forecast cannot place,
            and the fix for each row is one click in EPMS. */}
        {undated && undated.bills > 0 && (
          <div className="mt-6">
            <h3 className="mb-1 flex items-center gap-1.5 text-sm font-semibold text-neutral-800">
              <HelpCircle className="h-4 w-4 text-neutral-400" />
              No payment term on file — {money(undated.amount)} {currency} the forecast cannot place
            </h3>
            <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-neutral-500">
              NC bills these suppliers, but we hold no payment term for them, so there is no
              due date to calculate. They are excluded from both tables above rather than
              defaulted to Net 30. <strong>Create the vendor record</strong> (EPMS &rsquo;Vendors&rsquo; →
              From ERP), or set the term on the one that exists, and the balance moves into
              the forecast on the next load.
            </p>
            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full min-w-[720px] text-sm">
                <thead>
                  <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                    <th className="px-3 py-2 text-left font-medium">Supplier</th>
                    <th className="px-3 py-2 text-left font-medium">What is missing</th>
                    <th className="px-3 py-2 text-right font-medium">Bills</th>
                    <th className="px-3 py-2 text-right font-medium">Open balance</th>
                    <th className="px-3 py-2 text-left font-medium">Bills dated</th>
                  </tr>
                </thead>
                <tbody>
                  {(missing?.items.length ?? 0) === 0 ? (
                    <tr><td colSpan={5} className="px-3 py-6 text-center text-neutral-400">
                      Loading…
                    </td></tr>
                  ) : missing!.items.map((r) => (
                    <tr key={(r.supplier_code ?? '') + r.currency} className="border-t border-neutral-100">
                      <td className="px-3 py-2">
                        <div className="text-neutral-800">{r.supplier_name ?? '—'}</div>
                        <div className="font-mono text-[11px] text-neutral-400">{r.supplier_code ?? '—'}</div>
                      </td>
                      <td className="px-3 py-2 text-xs">
                        {r.reason === 'no_vendor_record' ? (
                          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">
                            no vendor record
                          </span>
                        ) : (
                          <span className="rounded bg-neutral-200 px-1.5 py-0.5 text-[10px] font-semibold text-neutral-600">
                            term &ldquo;{r.payment_terms}&rdquo; not recognised
                          </span>
                        )}
                      </td>
                      <td className={cell}>{r.bills}</td>
                      <td className={cn(cell, 'font-semibold')}>{money(r.amount)}</td>
                      <td className="px-3 py-2 text-xs text-neutral-500">
                        {day(r.oldest_bill)} – {day(r.newest_bill)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {bucket && (
          <div className="mt-6">
            <h3 className="mb-2 text-sm font-semibold text-neutral-800">
              {row?.buckets.find((b) => b.key === bucket)?.label}
              <span className="ml-2 font-normal text-neutral-500">
                {items?.total ?? 0} bills · {money(items?.amount)} {currency}
              </span>
            </h3>
            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full min-w-[900px] text-sm">
                <thead>
                  <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                    <th className="px-3 py-2 text-left font-medium">Bill</th>
                    <th className="px-3 py-2 text-left font-medium">Supplier</th>
                    <th className="px-3 py-2 text-left font-medium">Invoice no.</th>
                    <th className="px-3 py-2 text-left font-medium">PO</th>
                    <th className="px-3 py-2 text-left font-medium">Bill date</th>
                    <th className="px-3 py-2 text-left font-medium">Term</th>
                    <th className="px-3 py-2 text-left font-medium">Due</th>
                    <th className="px-3 py-2 text-right font-medium">Open</th>
                  </tr>
                </thead>
                <tbody>
                  {loadingItems && !items ? (
                    <tr><td colSpan={8} className="px-3 py-8 text-center">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></td></tr>
                  ) : itemsError ? (
                    // A failed request must never read as "nothing here".
                    <tr><td colSpan={8} className="px-3 py-8 text-center text-danger-700">
                      Could not load these bills: {String((itemsError as Error).message ?? itemsError)}
                    </td></tr>
                  ) : (items?.items.length ?? 0) === 0 ? (
                    <tr><td colSpan={8} className="px-3 py-8 text-center text-neutral-400">
                      Nothing in this bucket.</td></tr>
                  ) : items!.items.map((r) => (
                    <tr key={r.bill_no} className="border-t border-neutral-100 hover:bg-neutral-50/60">
                      <td className="px-3 py-2 font-mono text-xs">{r.bill_no}</td>
                      <td className="px-3 py-2">
                        <div className="text-neutral-800">{r.supplier_name ?? '—'}</div>
                        <div className="font-mono text-[11px] text-neutral-400">{r.supplier_code ?? '—'}</div>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-600">{r.invoice_no ?? '—'}</td>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-500">{r.purchase_order ?? '—'}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs text-neutral-600">{day(r.bill_date)}</td>
                      <td className="px-3 py-2 text-xs text-neutral-500">
                        {r.payment_terms ?? '—'}
                        {r.term_days !== null && <span className="text-neutral-400"> ({r.term_days}d)</span>}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs">
                        {day(r.due_date)}
                        {r.days_to_due !== null && (
                          <span className={cn('ml-1.5 text-[11px]',
                                              r.days_to_due < 0 ? 'text-amber-700' : 'text-neutral-400')}>
                            {r.days_to_due < 0 ? `${-r.days_to_due}d late` : `in ${r.days_to_due}d`}
                          </span>
                        )}
                      </td>
                      <td className={cn(cell, 'font-semibold')}>{money(r.open_bal)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </PortalChromeLayout>
  )
}
