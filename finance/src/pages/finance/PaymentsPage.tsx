/**
 * Payments hub (Task 12) — the only place that shows every payment made,
 * single or batched, in one list. Payment Batches only shows payments that
 * went through a run; single payments (e.g. one-off cheques) were invisible
 * before this page. It is also the only place remittance advice can be sent
 * for a payment that was never part of a batch — batch remittance lives on
 * Payment Batches (Task 13).
 *
 * Read-only plus remittance: no voiding, no reversing here.
 *
 * The detail drawer is fed straight from the row already loaded by the list
 * query, not from `GET /payments/{id}` — that endpoint returns the raw
 * record without the `payee_name` / `remittance_status` enrichment the list
 * endpoint adds (see finance-api/app/api/v1/payments.py), so a claim payment
 * fetched individually would show a blank payee even though the list knew
 * the employee's name.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { financeApi, financeDownload } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { RemittanceStatusBadge, type RemittanceStatus } from '@/components/remittance/RemittancePanel'
import { RemittanceDialog } from '@/components/remittance/RemittanceDialog'
import { secondaryBtn } from '@/components/remittance/buttonStyles'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

interface PaymentRow {
  id: string
  doc_kind: string | null
  doc_number: string | null
  payee_name: string | null
  payment_date: string
  payment_method: string
  reference: string | null
  amount: string
  currency: string
  status: string
  batch_id: string | null
  /** 'sent' | 'not_sent' — always filled by the list endpoint. */
  remittance_status: string | null
}

interface SummaryRow { currency: string; count: number; total: string }

interface Filters {
  date_from: string
  date_to: string
  q: string
  currency: string
  payment_method: string
  source: string
  remittance: string
}

const EMPTY_FILTERS: Filters = {
  date_from: '', date_to: '', q: '',
  currency: '', payment_method: '', source: '', remittance: '',
}

const PAGE_SIZE = 50
const CURRENCIES = ['CAD', 'USD', 'CNY', 'EUR']

function toQuery(f: object): string {
  const p = new URLSearchParams()
  Object.entries(f).forEach(([k, v]) => { if (v) p.set(k, String(v)) })
  return p.toString()
}

function fmtMoney(v: string, ccy: string): string {
  return `${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${ccy}`
}

const PAYMENT_STATUS_STYLE: Record<string, string> = {
  pending: 'bg-amber-50 text-amber-700',
  completed: 'bg-green-50 text-green-700',
  cancelled: 'bg-neutral-100 text-neutral-400',
}

/** Local badge for the payment lifecycle (pending/completed/cancelled),
 * following JvStatusBadge's convention (JvDetailModal.tsx) — there is no
 * shared `StatusBadge` in `@uniops/shell`. */
function PaymentStatusBadge({ status }: { status: string }) {
  return (
    <span className={cn('inline-flex rounded-full px-2 py-0.5 text-xs font-medium capitalize',
      PAYMENT_STATUS_STYLE[status] ?? 'bg-neutral-100 text-neutral-600')}>
      {status}
    </span>
  )
}

/**
 * The list only ever reports the binary 'sent' / 'not_sent' for this column
 * (see `_remittance_status` in payments.py — a pure existence check on
 * notification history, not a sendability check). 'not_sent' must map to its
 * own distinct status, NOT 'ready': 'ready' in `RemittancePanel` means the
 * live preview confirmed the payee has an email / invoice number and can
 * actually be sent to (`readinessOf`'s `block_reasons.length === 0` branch).
 * A payment with no notification history reads identically whether its
 * payee is perfectly sendable or missing an email entirely — collapsing
 * that into 'ready' would tell the operator "nothing to fix" about exactly
 * the stuck rows this page exists to surface. Open the drawer for the real
 * answer.
 */
function remittanceColumnStatus(v: string | null): RemittanceStatus {
  return v === 'sent' ? 'sent' : 'not_sent'
}

export default function PaymentsPage() {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  // Search text only, debounced: `filters.q` drives the input itself (so
  // typing feels instant) while `debouncedQ` drives the queries below, so a
  // fast typist doesn't re-run the list/summary query on every keystroke.
  // Page-reset-on-filter-change is unaffected — `setFilter` resets `page`
  // synchronously on every keystroke, same as any other filter, regardless
  // of when the debounced query actually fires.
  const [debouncedQ, setDebouncedQ] = useState(filters.q)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(filters.q), 300)
    return () => clearTimeout(t)
  }, [filters.q])

  const [page, setPage] = useState(1)
  const [openRow, setOpenRow] = useState<PaymentRow | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  // The filters actually sent to the server: same as `filters`, except `q`
  // is the debounced value. Everything downstream (qs/list/summary/export)
  // reads this, never raw `filters`, so search stays debounced everywhere.
  const queryFilters = useMemo<Filters>(() => ({ ...filters, q: debouncedQ }), [filters, debouncedQ])

  // Filters only — page/page_size deliberately excluded so /summary and
  // /export (both page-independent) share this exact query string with the
  // list, and changing page never re-triggers them.
  const qs = useMemo(() => toQuery(queryFilters), [queryFilters])

  const setFilter = (patch: Partial<Filters>) => {
    setPage(1)
    setFilters((f) => ({ ...f, ...patch }))
  }

  const { data: list, isLoading } = useQuery({
    queryKey: ['payments', qs, page],
    queryFn: () => financeApi.get<{ items: PaymentRow[]; total: number }>(
      `/payments?${toQuery({ ...queryFilters, page: String(page), page_size: String(PAGE_SIZE) })}`),
  })
  const rows = list?.items ?? []
  const total = list?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // Totals over the WHOLE filtered set, not the visible page — wired to the
  // filters only, never to `page`.
  const { data: summary = [] } = useQuery({
    queryKey: ['payments-summary', qs],
    queryFn: () => financeApi.get<SummaryRow[]>(`/payments/summary?${qs}`),
  })

  async function handleExport() {
    setExporting(true)
    setExportError(null)
    try {
      // Filters applied, pagination ignored — matches /payments/export.
      await financeDownload(`/payments/export?${qs}`, 'payments.csv')
    } catch (e) {
      setExportError((e as Error).message)
    } finally {
      setExporting(false)
    }
  }

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/payments"
      title="Payments"
      subtitle="Every payment made, single or batched — send remittance advice for the ones a batch never covered"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex flex-wrap gap-4">
          {summary.map((s) => (
            <div key={s.currency} className="rounded-lg border border-neutral-200 bg-white px-5 py-4">
              <p className="text-xs uppercase tracking-wide text-neutral-500">Total Paid ({s.currency})</p>
              <p className="mt-1 font-mono text-2xl font-bold text-neutral-900">
                {Number(s.total).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </p>
              <p className="text-xs text-neutral-500">{s.count} payment{s.count === 1 ? '' : 's'}</p>
            </div>
          ))}
          {summary.length === 0 && (
            <div className="rounded-lg border border-dashed border-neutral-200 px-5 py-4 text-sm text-neutral-400">
              No payments match the current filters.
            </div>
          )}
        </div>

        <div className="mb-3 flex flex-wrap items-end gap-3">
          <label className="text-xs text-neutral-600">
            From
            <input type="date" value={filters.date_from} className={cn(inputCls, 'block')}
                   onChange={(e) => setFilter({ date_from: e.target.value })} />
          </label>
          <label className="text-xs text-neutral-600">
            To
            <input type="date" value={filters.date_to} className={cn(inputCls, 'block')}
                   onChange={(e) => setFilter({ date_to: e.target.value })} />
          </label>
          <label className="text-xs text-neutral-600">
            Search
            <input value={filters.q} placeholder="Document, payee, reference" className={cn(inputCls, 'block w-56')}
                   onChange={(e) => setFilter({ q: e.target.value })} />
          </label>
          <label className="text-xs text-neutral-600">
            Currency
            <select value={filters.currency} className={cn(inputCls, 'block w-24')}
                    onChange={(e) => setFilter({ currency: e.target.value })}>
              <option value="">All</option>
              {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
          <label className="text-xs text-neutral-600">
            Method
            <select value={filters.payment_method} className={cn(inputCls, 'block w-28')}
                    onChange={(e) => setFilter({ payment_method: e.target.value })}>
              <option value="">All</option>
              <option value="eft">EFT</option>
              <option value="cheque">Cheque</option>
              <option value="wire">Wire</option>
              <option value="other">Other</option>
            </select>
          </label>
          <label className="text-xs text-neutral-600">
            Source
            <select value={filters.source} className={cn(inputCls, 'block w-24')}
                    onChange={(e) => setFilter({ source: e.target.value })}>
              <option value="">All</option>
              <option value="batch">Batch</option>
              <option value="single">Single</option>
            </select>
          </label>
          <label className="text-xs text-neutral-600">
            Remittance
            <select value={filters.remittance} className={cn(inputCls, 'block w-28')}
                    onChange={(e) => setFilter({ remittance: e.target.value })}>
              <option value="">All</option>
              <option value="sent">Sent</option>
              <option value="not_sent">Not sent</option>
            </select>
          </label>
          <button type="button" onClick={() => void handleExport()} disabled={exporting}
                  className={cn(secondaryBtn, 'ml-auto')}>
            <Download className="h-4 w-4" />
            {exporting ? 'Exporting…' : 'Export CSV'}
          </button>
        </div>

        {exportError && <div className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{exportError}</div>}

        <div className="overflow-hidden rounded-lg border border-neutral-200">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
              <tr>
                <th className="px-3 py-2">Date</th>
                <th className="px-3 py-2">Document</th>
                <th className="px-3 py-2">Payee</th>
                <th className="px-3 py-2 text-right">Amount</th>
                <th className="px-3 py-2">Method</th>
                <th className="px-3 py-2">Source</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2" title="Send history only — open a row to check whether it can actually be sent">Remittance</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">Loading…</td></tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No payments match the current filters.</td></tr>
              )}
              {rows.map((r, i) => (
                <tr key={r.id} className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-50', i % 2 && 'bg-neutral-50/40')}
                    onClick={() => setOpenRow(r)}>
                  <td className="px-3 py-2 text-xs text-neutral-600">{r.payment_date}</td>
                  <td className="px-3 py-2 font-mono text-xs">{r.doc_number ?? '—'}</td>
                  <td className="px-3 py-2">{r.payee_name ?? '—'}</td>
                  <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.amount, r.currency)}</td>
                  <td className="px-3 py-2 text-xs uppercase text-neutral-500">{r.payment_method}</td>
                  <td className="px-3 py-2 text-xs text-neutral-500">{r.batch_id ? 'Batch' : 'Single'}</td>
                  <td className="px-3 py-2"><PaymentStatusBadge status={r.status} /></td>
                  <td className="px-3 py-2"><RemittanceStatusBadge status={remittanceColumnStatus(r.remittance_status)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-3 flex items-center justify-between text-xs text-neutral-500">
          <span>{total} payment{total === 1 ? '' : 's'}</span>
          {pageCount > 1 && (
            <div className="flex items-center gap-2">
              <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
                      className="rounded border border-neutral-300 px-2 py-1 disabled:opacity-40">Prev</button>
              <span>Page {page} of {pageCount}</span>
              <button type="button" disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}
                      className="rounded border border-neutral-300 px-2 py-1 disabled:opacity-40">Next</button>
            </div>
          )}
        </div>
      </div>

      {openRow && <PaymentDetailModal row={openRow} onClose={() => setOpenRow(null)} />}
    </PortalChromeLayout>
  )
}

/**
 * Uses `RemittanceDialog`'s shared modal shell (backdrop, panel, X button,
 * Close button — see Tasks 12/13) with a payment-specific header slotted in,
 * rather than a second copy of that shell.
 */
function PaymentDetailModal({ row, onClose }: { row: PaymentRow; onClose: () => void }) {
  return (
    <RemittanceDialog
      open
      onClose={onClose}
      scope={{ kind: 'payment', id: row.id }}
      header={
        <div>
          <h2 className="text-base font-semibold text-neutral-800">
            {row.doc_number ?? 'Payment'}
            <span className="ml-2"><PaymentStatusBadge status={row.status} /></span>
          </h2>
          <p className="mt-1 text-sm text-neutral-500">
            {row.payee_name ?? '—'} · <span className="font-mono">{fmtMoney(row.amount, row.currency)}</span> · {row.payment_date}
            {row.batch_id && <span className="ml-1 text-neutral-400">· part of a batch</span>}
          </p>
        </div>
      }
    />
  )
}
