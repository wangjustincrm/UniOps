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
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Download, Send, X } from 'lucide-react'
import { financeApi, financeDownload, mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { RemittanceStatusBadge, type RemittanceStatus } from '@/components/remittance/RemittancePanel'
import { RemittanceDialog } from '@/components/remittance/RemittanceDialog'
import { primaryBtn, secondaryBtn } from '@/components/remittance/buttonStyles'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

interface PaymentRow {
  id: string
  doc_kind: string | null
  doc_number: string | null
  payee_name: string | null
  payment_date: string
  payment_method: string
  reference: string | null
  /** Decimal-as-string. The NET cash that left the bank — a vendor credit may
   * have reduced it. Gross = Number(amount) + Number(credit_applied). */
  amount: string
  /** Decimal-as-string. Vendor credit netted off this payment; "0.00" when
   * none was (which is every payment before Phase B, and every expense-claim
   * payment). */
  credit_applied: string
  /** Which credit note(s) made up credit_applied — the vendor's own number
   * plus what THAT note contributed. A payment can net more than one; empty
   * whenever credit_applied is "0.00". */
  credit_notes: { vendor_credit_number: string; applied_amount: string }[]
  currency: string
  status: string
  batch_id: string | null
  /** Business-partner id for vendor payments; null for expense-claim payments.
   * The payee-identity key for the one-payee guard — stable across a vendor
   * rename, unlike the `payee_name` snapshot. */
  vendor_id: string | null
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
  /** Business partner id — same field the backend's `PaymentFilters.vendor_id`
   * already accepts and `payment_crud.get_all` already honours; this page
   * simply hadn't had a picker to set it. */
  vendor_id: string
}

const EMPTY_FILTERS: Filters = {
  date_from: '', date_to: '', q: '',
  currency: '', payment_method: '', source: '', remittance: '', vendor_id: '',
}

const PAGE_SIZE = 50
const CURRENCIES = ['CAD', 'USD', 'CNY', 'EUR']

interface VendorOption { id: string; name: string; code: string }

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

/**
 * Vendor typeahead for the filter bar. Vendors can number in the hundreds,
 * so this hits `GET /mdm/v1/partners?role=supplier&search=...` (debounced,
 * same 300ms as the hub's own search box) rather than loading a giant
 * static `<select>`.
 *
 * The dropdown is `createPortal`ed to `document.body` and positioned
 * `fixed` off the trigger's own bounding rect (per this app's overlay
 * convention — a plain absolutely-positioned dropdown gets clipped by any
 * ancestor `overflow` and this filter bar sits inside several). Click-outside
 * excludes both the trigger and the portaled overlay via two refs, not just
 * one, since the overlay is not a DOM descendant of the trigger once
 * portaled.
 */
function VendorFilter({ vendorId, vendorName, onChange }: {
  vendorId: string
  vendorName: string
  onChange: (vendor: { id: string; name: string } | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState(vendorName)
  const [debouncedQuery, setDebouncedQuery] = useState(vendorName)
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null)
  const triggerRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)

  // Keep the input text in sync when the selection is cleared/changed from
  // outside this component (e.g. the filter bar's own "All" reset), not just
  // in response to a pick made here.
  useEffect(() => { setQuery(vendorName) }, [vendorName])

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 300)
    return () => clearTimeout(t)
  }, [query])

  const { data } = useQuery({
    queryKey: ['vendor-typeahead', debouncedQuery],
    queryFn: () => mdmApi.get<{ items: VendorOption[] }>(
      `/partners?role=supplier&search=${encodeURIComponent(debouncedQuery)}&page_size=50`),
    enabled: open,
  })
  const options = data?.items ?? []

  useEffect(() => {
    function onDocMouseDown(e: MouseEvent) {
      const target = e.target as Node
      if (triggerRef.current?.contains(target)) return
      if (overlayRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [])

  function openDropdown() {
    const r = triggerRef.current?.getBoundingClientRect()
    if (r) setRect({ top: r.bottom + 4, left: r.left, width: Math.max(r.width, 240) })
    setOpen(true)
  }

  return (
    <div ref={triggerRef} className="relative">
      <input
        value={query}
        placeholder="All vendors"
        className={cn(inputCls, 'block w-48 pr-6')}
        onFocus={openDropdown}
        onChange={(e) => {
          setQuery(e.target.value)
          if (vendorId) onChange(null)
          openDropdown()
        }}
      />
      {vendorId && (
        <button type="button" title="Clear vendor filter"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-700"
                onClick={() => { onChange(null); setQuery(''); setOpen(false) }}>
          <X className="h-3.5 w-3.5" />
        </button>
      )}
      {open && createPortal(
        <div ref={overlayRef}
             style={{ position: 'fixed', top: rect?.top, left: rect?.left, width: rect?.width, zIndex: 60 }}
             className="max-h-64 overflow-y-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
          {options.length === 0 && (
            <div className="px-3 py-2 text-xs text-neutral-400">No vendors found</div>
          )}
          {options.map((v) => (
            <button key={v.id} type="button"
                    className="block w-full px-3 py-1.5 text-left text-sm hover:bg-neutral-50"
                    onClick={() => { onChange({ id: v.id, name: v.name }); setQuery(v.name); setOpen(false) }}>
              {v.name} <span className="text-xs text-neutral-400">({v.code})</span>
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  )
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

  // Display name for the selected vendor filter — `filters.vendor_id` alone
  // (what's actually sent to the server) has no name to show back in the
  // typeahead's input once selected.
  const [vendorName, setVendorName] = useState('')

  // Row multi-select for the "send one remittance for several payments"
  // action. Selection is page-scoped only: it is cleared whenever the
  // filtered set or the page changes (below), not carried across pages —
  // there is no cross-page persistence here, by design (see report).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // The ids the selection dialog is open for — captured once, at the moment
  // "Send remittance" is clicked, and independent of `selectedIds` from then
  // on. This deliberately does NOT read `selectedIds` live: a successful
  // send clears the table's checkboxes (below) so the operator returns to a
  // clean page, but the dialog must keep showing the SAME scope it was
  // opened with while it does — if it re-derived `paymentIds` from
  // `selectedIds` on every render, clearing the checkboxes mid-dialog would
  // shrink the scope to `[]` and the panel would flip to "no payees to
  // email" right under the send result it just produced. `null` = closed.
  const [selectionScopeIds, setSelectionScopeIds] = useState<string[] | null>(null)

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

  // The filtered set or the visible page changed — the checked ids may no
  // longer even be on screen, so drop the selection rather than carry stale
  // ids forward silently.
  useEffect(() => { setSelectedIds(new Set()) }, [qs, page])

  const selectedRows = rows.filter((r) => selectedIds.has(r.id))
  // Payee identity for the one-payee guard is `vendor_id`, not `payee_name`:
  // the backend groups remittance by vendor_id, and vendor_name is a per-payment
  // snapshot that diverges across a vendor rename — comparing names would wrongly
  // block the exact "one vendor paid across several payments" case this feature
  // exists for. Expense-claim rows have no vendor_id; fall back to their
  // employee payee_name for those. A genuinely payee-less row keys as ""
  // (harmless — it shows blocked in the panel and cannot be sent anyway).
  const selectedPayeeKeys = new Set(
    selectedRows.map((r) => r.vendor_id ?? r.payee_name ?? ''),
  )
  const singlePayee = selectedPayeeKeys.size <= 1

  // Header context for the (possibly already-cleared, see above) selection
  // dialog — derived from the frozen `selectionScopeIds`, not from live
  // `selectedIds`/`selectedRows`.
  const dialogPayeeName = selectionScopeIds
    ? rows.find((r) => selectionScopeIds.includes(r.id))?.payee_name ?? null
    : null

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

  function toggleRow(id: string, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  // Header checkbox selects/clears the CURRENT PAGE only — selection does
  // not persist across pages (see the `useEffect` above), so "select all"
  // only ever means "all rows visible right now".
  const allOnPageSelected = rows.length > 0 && rows.every((r) => selectedIds.has(r.id))
  function toggleAllOnPage(checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      for (const r of rows) {
        if (checked) next.add(r.id)
        else next.delete(r.id)
      }
      return next
    })
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
          <label className="text-xs text-neutral-600">
            Vendor
            <VendorFilter
              vendorId={filters.vendor_id}
              vendorName={vendorName}
              onChange={(v) => {
                setVendorName(v?.name ?? '')
                setFilter({ vendor_id: v?.id ?? '' })
              }}
            />
          </label>
          <button type="button" onClick={() => void handleExport()} disabled={exporting}
                  className={cn(secondaryBtn, 'ml-auto')}>
            <Download className="h-4 w-4" />
            {exporting ? 'Exporting…' : 'Export CSV'}
          </button>
        </div>

        {exportError && <div className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{exportError}</div>}

        {selectedIds.size > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-primary-200 bg-primary-50 px-4 py-2">
            <span className="text-sm font-medium text-neutral-800">
              {selectedIds.size} payment{selectedIds.size === 1 ? '' : 's'} selected
            </span>
            {!singlePayee && (
              <span className="text-xs text-amber-700">
                Selected payments must all be for the same payee to send one remittance advice — narrow the selection or filter by vendor first.
              </span>
            )}
            <div className="ml-auto flex items-center gap-2">
              <button type="button" className={secondaryBtn} onClick={() => setSelectedIds(new Set())}>
                Clear
              </button>
              <button type="button" className={primaryBtn} disabled={!singlePayee}
                      onClick={() => setSelectionScopeIds([...selectedIds])}>
                <Send className="h-4 w-4" />
                Send remittance
              </button>
            </div>
          </div>
        )}

        <div className="overflow-hidden rounded-lg border border-neutral-200">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
              <tr>
                <th className="w-8 px-3 py-2">
                  <input type="checkbox" checked={allOnPageSelected}
                         onChange={(e) => toggleAllOnPage(e.target.checked)}
                         aria-label="Select all payments on this page" />
                </th>
                <th className="px-3 py-2">Date</th>
                <th className="px-3 py-2">Document</th>
                <th className="px-3 py-2">Payee</th>
                <th className="px-3 py-2 text-right" title="Cash that left the bank, net of any vendor credit">Amount</th>
                <th className="px-3 py-2 text-right" title="Vendor credit netted off this payment. Gross = Amount + Credit.">Credit</th>
                <th className="px-3 py-2">Method</th>
                <th className="px-3 py-2">Source</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2" title="Send history only — open a row to check whether it can actually be sent">Remittance</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={10} className="px-3 py-6 text-center text-neutral-400">Loading…</td></tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={10} className="px-3 py-6 text-center text-neutral-400">No payments match the current filters.</td></tr>
              )}
              {rows.map((r, i) => (
                <tr key={r.id} className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-50', i % 2 && 'bg-neutral-50/40')}
                    onClick={() => setOpenRow(r)}>
                  <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selectedIds.has(r.id)}
                           onChange={(e) => toggleRow(r.id, e.target.checked)}
                           aria-label={`Select payment ${r.doc_number ?? r.id}`} />
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-600">{r.payment_date}</td>
                  <td className="px-3 py-2 font-mono text-xs">{r.doc_number ?? '—'}</td>
                  <td className="px-3 py-2">{r.payee_name ?? '—'}</td>
                  <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.amount, r.currency)}</td>
                  {/* Without this the short payment has no explanation inside
                      the app at all — `amount` is already net. Dash rather
                      than "0.00" so a genuinely netted row stands out. */}
                  <td className="px-3 py-2 text-right font-mono text-xs text-neutral-500">
                    {Number(r.credit_applied ?? 0) > 0 ? fmtMoney(r.credit_applied, r.currency) : '—'}
                  </td>
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

      {selectionScopeIds && (
        <RemittanceDialog
          open
          onClose={() => setSelectionScopeIds(null)}
          // A successful send only clears the underlying table selection
          // (so the operator returns to unchecked rows) — it must NOT touch
          // `selectionScopeIds` itself, or the dialog still open above it
          // would suddenly be scoped to an empty selection (see that state's
          // docstring).
          onSent={() => setSelectedIds(new Set())}
          scope={{ kind: 'selection', paymentIds: selectionScopeIds }}
          header={
            <div>
              <h2 className="text-base font-semibold text-neutral-800">Send Remittance Advice</h2>
              <p className="mt-1 text-sm text-neutral-500">
                {selectionScopeIds.length} payment{selectionScopeIds.length === 1 ? '' : 's'}
                {dialogPayeeName && <> · {dialogPayeeName}</>}
              </p>
            </div>
          }
        />
      )}
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
            {row.payee_name ?? '—'} · <span className="font-mono">{fmtMoney(row.amount, row.currency)}</span>
            {Number(row.credit_applied ?? 0) > 0 && (
              <span className="text-neutral-400">
                {' '}(gross <span className="font-mono">{fmtMoney(String(Number(row.amount) + Number(row.credit_applied)), row.currency)}</span>
                {' '}less <span className="font-mono">{fmtMoney(row.credit_applied, row.currency)}</span> vendor credit)
              </span>
            )} · {row.payment_date}
            {row.batch_id && <span className="ml-1 text-neutral-400">· part of a batch</span>}
          </p>
          {/* The table column stays a single netted figure; this is the one
              place that names which credit note(s) made it up — never just
              the total, since a payment can net more than one. */}
          {row.credit_notes && row.credit_notes.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-sm text-neutral-500">
              {row.credit_notes.map((cn, i) => (
                <li key={i}>
                  Vendor credit <span className="font-mono">{cn.vendor_credit_number}</span>
                  {' '}<span className="font-mono">{fmtMoney(cn.applied_amount, row.currency)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      }
    />
  )
}
