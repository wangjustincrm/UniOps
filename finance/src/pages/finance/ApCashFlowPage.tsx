/**
 * AP Cash Flow — what we have to pay, and when.
 *
 * The question finance has been asking all along: which payables are due, and
 * how much cash the next quarter needs. Built on NC's approved open balance,
 * because NC is the book of record for payable balances — but only the part an
 * approved EPMS PA is about to pay, with reversal pairs netted out (finance,
 * 2026-11-02; finance-api services/ap_cash_flow). Approved prepayment PAs and
 * expense claims never reach NC as a payable, so they are added as rows of
 * their own, due on approval (finance, 2026-11-05). A bill's invoice date and
 * due date are the EPMS invoice's, not NC's bill date + vendor term (finance,
 * 2026-11-11); a bill whose invoices fall due on different days is one row per
 * due date.
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
import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Download, HelpCircle, Loader2, Search } from 'lucide-react'
import { Button, ChoiceCard, Input, SortTh, TBody, THead, Table, Td, Th, Tr, sortParams, useSort, useSortableRows, Badge } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn, formatDate, formatDateTime } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { ListPager } from '@/components/ListPager'
import { BillInvoices, billPurchaseOrders, invoicesCsv, type BillInvoice } from '@/components/ap/BillInvoices'

const BILL_PAGE_SIZE = 100
/** GET /ap-cash-flow/items sort keys (finance-api ap_cash_flow.ITEM_SORT). */
type BillSortKey = 'bill_no' | 'supplier' | 'invoice_no' | 'purchase_order' | 'invoice_date'
  | 'payment_terms' | 'due_date' | 'open_bal' | 'pa'

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

/** Only a bill has an AP number; `doc_key` is unique across all three kinds
 *  (a bill split by invoice due date has one AP number on two rows). */
type DocKind = 'bill' | 'prepayment' | 'claim'
const DOC_KIND_LABEL: Record<DocKind, string> = {
  bill: 'NC bill', prepayment: 'Prepayment', claim: 'Claim',
}
const DUE_FROM_LABEL: Record<BillRow['due_from'], string> = {
  invoice: 'EPMS invoice', vendor_term: 'NC bill date + vendor term', approval: 'Approval date',
}

interface BillRow {
  doc_kind: DocKind; doc_key: string
  bill_no: string | null; currency: string
  /** NC's bill date — reference only; it no longer decides the due date. */
  bill_date: string | null
  /** EPMS invoice date for a bill; approval date for a prepayment or claim. */
  invoice_date: string | null
  supplier_code: string | null; supplier_name: string | null
  /** One per invoice number on the bill's lines — usually one, often several. */
  invoices: BillInvoice[]; trade_type: string | null
  open_bal: string
  /** The approved PA(s) behind this bill, or the prepayment PA / claim itself. */
  pa_numbers: string[]
  payment_terms: string | null; term_days: number | null
  due_date: string | null; days_to_due: number | null
  /** invoice: EPMS invoice due date. vendor_term: NC bill date + vendor term
   *  (fallback). approval: prepayment / claim, due on approval. */
  due_from: 'invoice' | 'vendor_term' | 'approval'
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
interface MissingResp {
  total: number
  /** The single date carrying the most undated money — an opening-balance load
   *  looks exactly like this, and it changes what finance should do about it. */
  largest_single_date: { bill_date: string; bills: number; amount: string } | null
  items: MissingRow[]
}

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
function day(v: string | null) { return v ? formatDate(v.slice(0, 10)) : '—' }

const cell = 'px-3 py-2 text-right font-mono tabular-nums'

export default function ApCashFlowPage() {
  const { user } = useAuthStore()
  const [currency, setCurrency] = useState('CAD')
  const [bucket, setBucket] = useState<string | null>(null)
  // A selection can also be one supplier out of the undated list — every
  // figure on this page has to be traceable back to the documents behind it.
  const [supplier, setSupplier] = useState<{ code: string; name: string | null } | null>(null)

  // Searches bill no, invoice no, supplier and PA no on the server, inside the
  // current bucket / supplier if one is picked, else across the currency.
  // Debounced so a fast typist does not fire a query per keystroke.
  const [searchQ, setSearchQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(searchQ.trim()), 300)
    return () => clearTimeout(t)
  }, [searchQ])
  const searching = debouncedQ !== ''

  const { data: summary, isFetching: loadingSummary } = useQuery({
    queryKey: ['ap-cash-flow-summary'],
    queryFn: () => financeApi.get<SummaryResp>('/ap-cash-flow/summary'),
  })
  // The bill drill-down is paged and sorted by the API. The page belongs to
  // the selection it was turned on: picking another bucket or supplier (or
  // currency) starts again at page 1, and so does a new sort.
  const selKey = `${currency}|${bucket ?? ''}|${supplier?.code ?? ''}|${debouncedQ}`
  const [billPageAt, setBillPageAt] = useState({ key: selKey, page: 1 })
  const billPage = billPageAt.key === selKey ? billPageAt.page : 1
  const setBillPage = (page: number) => setBillPageAt({ key: selKey, page })
  const [billSort, onBillSort] = useSort<BillSortKey>(null, () => setBillPage(1))
  const { sort: billSortKey, dir: billSortDir } = sortParams(billSort)
  const { data: items, isFetching: loadingItems, error: itemsError } = useQuery({
    queryKey: ['ap-cash-flow-items', currency, bucket, supplier?.code, debouncedQ, billPage, billSort],
    queryFn: () => financeApi.get<ItemsResp>(
      `/ap-cash-flow/items?currency=${currency}` +
      `${bucket ? `&bucket=${bucket}` : ''}` +
      `${supplier ? `&supplier_code=${encodeURIComponent(supplier.code)}` : ''}` +
      `${searching ? `&q=${encodeURIComponent(debouncedQ)}` : ''}` +
      `&limit=${BILL_PAGE_SIZE}&offset=${(billPage - 1) * BILL_PAGE_SIZE}` +
      `${billSortKey ? `&sort=${billSortKey}&dir=${billSortDir}` : ''}`),
    enabled: !!bucket || !!supplier || searching,
  })
  const { data: missing } = useQuery({
    queryKey: ['ap-cash-flow-missing', currency],
    queryFn: () => financeApi.get<MissingResp>(`/ap-cash-flow/missing-terms?currency=${currency}`),
  })

  // The missing-terms list comes back whole (one row per supplier); it sorts
  // on the page.
  const missingSort = useSortableRows(missing?.items, {
    supplier: (r) => r.supplier_name || r.supplier_code,
    reason: (r) => (r.reason === 'no_vendor_record' ? 'no vendor record'
      : `term "${r.payment_terms}" not recognised`),
    bills: (r) => r.bills, amount: (r) => Number(r.amount), dated: (r) => r.oldest_bill,
  })

  const selected = bucket || supplier || searching
  const clearSelection = () => { setBucket(null); setSupplier(null); setSearchQ('') }
  const pickBucket = (k: string) => {
    setSupplier(null)
    setBucket(bucket === k && !supplier ? null : k)
  }
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
        `/ap-cash-flow/items?currency=${currency}&limit=1000&offset=${off}` +
        // every bill of the currency, in the column order the drill-down uses
        `${billSortKey ? `&sort=${billSortKey}&dir=${billSortDir}` : ''}`)
      all.push(...page.items)
      if (all.length >= page.total || page.items.length === 0) break
    }
    const head = ['Type', 'AP no', 'Supplier code', 'Supplier', 'Invoice no', 'PO', 'PA', 'Currency',
                  'Invoice / approved date', 'NC bill date', 'Payment term', 'Term days',
                  'Due date', 'Due date from', 'Days to due',
                  'Open balance', 'Bucket', 'No vendor record']
    const body = all.map((r) => [
      DOC_KIND_LABEL[r.doc_kind], r.bill_no, r.supplier_code, r.supplier_name,
      invoicesCsv(r.invoices),
      billPurchaseOrders(r.invoices).join('; '), r.pa_numbers.join('; '),
      r.currency, r.invoice_date, r.bill_date, r.payment_terms, r.term_days, r.due_date,
      DUE_FROM_LABEL[r.due_from], r.days_to_due,
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
      subtitle="What approved PAs and expense claims will pay, and when"
    >
      <div className="mx-auto max-w-7xl">
        {summary?.mirror && (
          <div className={cn(
            'mb-4 flex flex-wrap items-center gap-2 rounded-lg border px-4 py-2.5 text-sm',
            summary.mirror.tie_out_ok === false
              ? 'border-danger-line bg-danger-soft text-danger-fg'
              : 'border-line bg-surface-subtle text-fg-secondary')}>
            {summary.mirror.tie_out_ok === false
              ? <AlertTriangle className="h-4 w-4 shrink-0" />
              : <CheckCircle2 className="h-4 w-4 shrink-0 text-primary-700" />}
            <span>
              Only NC bills whose invoice is on an <strong>approved EPMS PA</strong> are counted;
              a reversal and the bill it reverses (same supplier and invoice number, netting
              to zero) are left out. Invoice and due dates are the <strong>EPMS invoice&rsquo;s</strong>,
              found by the bill&rsquo;s invoice number and supplier; NC&rsquo;s bill date is when the
              payable was keyed, not when the invoice was issued. Approved <strong>prepayment PAs</strong> and{' '}
              <strong>expense claims</strong> have no NC bill: they are added at their own
              amount, due on the approval date. Mirror as of{' '}
              <strong>{summary.mirror.synced_at ? formatDateTime(summary.mirror.synced_at) : 'never'}</strong>
              {summary.mirror.tie_out_ok === false && ' — it does NOT tie out against NC.'}
            </span>
          </div>
        )}

        {loadingSummary && !summary ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : (
          <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(summary?.currencies ?? []).map((c) => (
              <ChoiceCard key={c.currency} selected={currency === c.currency} onClick={() => { setCurrency(c.currency); setBucket(null) }}
                      className="items-stretch gap-0 rounded-lg p-3">
                <span className="font-mono text-sm font-semibold">{c.currency}</span>
                <dl className="mt-2 space-y-1 text-xs">
                  {/* The forecast figure comes first because it is the only one
                      that answers "how much cash do we need". */}
                  <div className="flex justify-between gap-2">
                    <dt className="text-fg-muted">Coming due</dt>
                    <dd className="font-mono tabular-nums font-semibold text-primary-700">{money(c.forward)}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-fg-muted">Already overdue</dt>
                    <dd className="font-mono tabular-nums text-warning-fg">{money(c.overdue)}</dd>
                  </div>
                  <div className="flex justify-between gap-2 border-t border-line pt-1">
                    <dt className="text-fg-muted">No payment term</dt>
                    <dd className="font-mono tabular-nums text-fg-muted">{money(c.undated)}</dd>
                  </div>
                </dl>
                <p className="mt-1.5 text-2xs text-fg-muted">
                  {money(c.total)} open in total
                </p>
              </ChoiceCard>
            ))}
          </div>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-fg-muted">
            {currency} · as of {formatDate(summary?.as_of)}
          </span>
          {selected && (
            <Button variant="secondary" size="xs" onClick={clearSelection}
                   >
              Clear selection
            </Button>
          )}
          <div className="relative ml-auto">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
            <Input type="search" value={searchQ} onChange={(e) => setSearchQ(e.target.value)}
                   placeholder="Search AP, invoice, supplier, PA, claim"
                   aria-label="Search AP number, invoice number, supplier or employee, PA number or claim number"
                   className="w-72 pl-8 pr-2.5" />
          </div>
          <Button variant="secondary" size="sm" onClick={exportCsv}
                 >
            <Download className="h-4 w-4" /> Export CSV
          </Button>
        </div>

        <div className="grid gap-5 lg:grid-cols-2">
          <div className="min-w-0">
            <h3 className="mb-1 text-sm font-semibold text-fg">Coming due</h3>
            <p className="mb-2 text-2xs leading-relaxed text-fg-muted">
              Cumulative is the answer to &ldquo;how much cash do we need by then&rdquo; — each row
              includes everything above it.
            </p>
            <div className="overflow-x-auto rounded-lg border border-line">
              <Table density="compact" className="min-w-[420px]">
                <THead>
                  <Tr className="bg-surface-subtle text-xs text-fg-muted">
                    <Th>Horizon</Th>
                    <Th align="right">Bills</Th>
                    <Th align="right">Amount</Th>
                    <Th align="right">Cumulative</Th>
                  </Tr>
                </THead>
                <TBody>
                  {forwardCumulative.every((b) => b.bills === 0) ? (
                    <Tr><Td align="center" colSpan={4} className="text-fg-muted">
                      Nothing dated and not yet due in {currency}.
                    </Td></Tr>
                  ) : forwardCumulative.filter((b) => b.bills > 0).map((b) => (
                    <Tr key={b.key}
                        onClick={() => pickBucket(b.key)}
                        className={cn('cursor-pointer border-t border-line-subtle hover:bg-surface-subtle/60',
                                      bucket === b.key && !supplier && 'bg-primary-50/60')}>
                      <Td>
                        {b.label}
                        {b.first_due && (
                          <div className="text-2xs text-fg-muted">
                            {day(b.first_due)} – {day(b.last_due)}
                          </div>
                        )}
                      </Td>
                      <Td className={cell}>{b.bills}</Td>
                      <Td className={cell}>{money(b.amount)}</Td>
                      <Td className={cn(cell, 'font-semibold text-primary-700')}>
                        {money(String(b.cumulative))}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
          </div>

          <div className="min-w-0">
            <h3 className="mb-1 text-sm font-semibold text-fg">Already overdue</h3>
            <p className="mb-2 text-2xs leading-relaxed text-fg-muted">
              Aged, and kept out of the forecast above. The oldest band is mostly the stale
              tail NC can no longer clear — judge it on{' '}
              <a href="/finance/ap-ledger-health" className="text-primary-700 hover:underline">
                AP Subledger Health</a> and anything set aside there stops appearing here.
            </p>
            <div className="overflow-x-auto rounded-lg border border-line">
              <Table density="compact" className="min-w-[420px]">
                <THead>
                  <Tr className="bg-surface-subtle text-xs text-fg-muted">
                    <Th>Age</Th>
                    <Th align="right">Bills</Th>
                    <Th align="right">Amount</Th>
                  </Tr>
                </THead>
                <TBody>
                  {overdue.every((b) => b.bills === 0) ? (
                    <Tr><Td align="center" colSpan={3} className="text-fg-muted">
                      Nothing overdue in {currency}.
                    </Td></Tr>
                  ) : overdue.filter((b) => b.bills > 0).map((b) => (
                    <Tr key={b.key}
                        onClick={() => pickBucket(b.key)}
                        className={cn('cursor-pointer border-t border-line-subtle hover:bg-surface-subtle/60',
                                      bucket === b.key && !supplier && 'bg-primary-50/60')}>
                      <Td>
                        {b.label}
                        {b.first_due && (
                          <div className="text-2xs text-fg-muted">
                            due {day(b.first_due)} – {day(b.last_due)}
                          </div>
                        )}
                      </Td>
                      <Td className={cell}>{b.bills}</Td>
                      <Td className={cn(cell, 'font-semibold text-warning-fg')}>{money(b.amount)}</Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
          </div>
        </div>

        {selected && (
          <div className="mt-6">
            {/* Sits directly under whatever was clicked, not at the foot of
                the page: a detail table three screens below the selection
                reads as "clicking did nothing". */}
            <h3 className="mb-2 text-sm font-semibold text-fg">
              {supplier
                ? <>{supplier.name ?? supplier.code}
                    <span className="ml-1.5 font-mono text-xs font-normal text-fg-muted">
                      {supplier.code}</span></>
                : bucket ? row?.buckets.find((b) => b.key === bucket)?.label
                : `All ${currency} bills`}
              {searching && (
                <span className="ml-1.5 font-normal text-fg-muted">
                  matching &ldquo;{debouncedQ}&rdquo;</span>
              )}
              <span className="ml-2 font-normal text-fg-muted">
                {items?.total ?? 0} bills · {money(items?.amount)} {currency}
              </span>
              <span className="ml-2 text-xs font-normal text-fg-muted">
                every document behind that figure
              </span>
            </h3>
            <div className="overflow-x-auto rounded-lg border border-line">
              <Table density="compact" className="min-w-[900px]">
                <THead>
                  <Tr className="bg-surface-subtle text-xs text-fg-muted">
                    <SortTh label="AP no." sortKey="bill_no" sort={billSort} onSort={onBillSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="Supplier" sortKey="supplier" sort={billSort} onSort={onBillSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="Invoice no." sortKey="invoice_no" sort={billSort} onSort={onBillSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="PO" sortKey="purchase_order" sort={billSort} onSort={onBillSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="PA / Claim" sortKey="pa" sort={billSort} onSort={onBillSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="Invoice / approved" sortKey="invoice_date" sort={billSort} onSort={onBillSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="Term" sortKey="payment_terms" sort={billSort} onSort={onBillSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="Due" sortKey="due_date" sort={billSort} onSort={onBillSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="Open" sortKey="open_bal" sort={billSort} onSort={onBillSort} align="right" className="px-3 py-2 font-medium" />
                  </Tr>
                </THead>
                <TBody>
                  {loadingItems && !items ? (
                    <Tr><Td align="center" colSpan={9}>
                      <Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></Td></Tr>
                  ) : itemsError ? (
                    // A failed request must never read as "nothing here".
                    <Tr><Td align="center" colSpan={9} className="text-danger-fg">
                      Could not load these bills: {String((itemsError as Error).message ?? itemsError)}
                    </Td></Tr>
                  ) : (items?.items.length ?? 0) === 0 ? (
                    <Tr><Td align="center" colSpan={9} className="text-fg-muted">
                      {searching ? 'No bill matches the search.' : 'Nothing in this bucket.'}</Td></Tr>
                  ) : items!.items.map((r) => (
                    <Tr key={r.doc_key} className="hover:bg-surface-subtle/60">
                      <Td className="font-mono text-xs">
                        {r.bill_no ?? (
                          <Badge size="sm" className="font-sans">
                            {DOC_KIND_LABEL[r.doc_kind]}
                          </Badge>
                        )}
                      </Td>
                      <Td>
                        <div className="text-fg">{r.supplier_name ?? '—'}</div>
                        <div className="font-mono text-2xs text-fg-muted">{r.supplier_code ?? '—'}</div>
                      </Td>
                      <Td className="font-mono text-xs text-fg-secondary"><BillInvoices invoices={r.invoices} /></Td>
                      <Td className="font-mono text-xs text-fg-muted">{billPurchaseOrders(r.invoices).join(', ') || '—'}</Td>
                      <Td className="font-mono text-xs text-fg-muted">{r.pa_numbers.join(', ') || '—'}</Td>
                      <Td className="whitespace-nowrap text-xs text-fg-secondary">
                        {day(r.invoice_date)}
                        {r.doc_kind === 'bill' && r.bill_date && r.bill_date !== r.invoice_date && (
                          <div className="text-2xs text-fg-muted" title="NC bill date — when the payable was keyed in NC">
                            NC {day(r.bill_date)}
                          </div>
                        )}
                      </Td>
                      <Td className="text-xs text-fg-muted">
                        {r.due_from === 'invoice' ? (
                          <span title="Days from invoice date to the EPMS invoice due date">
                            {r.term_days !== null ? `${r.term_days}d` : '—'}
                          </span>
                        ) : (
                          <>
                            {r.payment_terms ?? '—'}
                            {r.term_days !== null && <span className="text-fg-muted"> ({r.term_days}d)</span>}
                          </>
                        )}
                      </Td>
                      <Td className="whitespace-nowrap text-xs">
                        {day(r.due_date)}
                        {r.days_to_due !== null && (
                          <span className={cn('ml-1.5 text-2xs',
                                              r.days_to_due < 0 ? 'text-warning-fg' : 'text-fg-muted')}>
                            {r.days_to_due < 0 ? `${-r.days_to_due}d late` : `in ${r.days_to_due}d`}
                          </span>
                        )}
                      </Td>
                      <Td className={cn(cell, 'font-semibold')}>{money(r.open_bal)}</Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
            <ListPager page={billPage} pageSize={BILL_PAGE_SIZE} total={items?.total ?? 0}
                       noun="bills" onPage={setBillPage} />
          </div>
        )}

        {/* Declared, never defaulted. This is money the forecast cannot place,
            and the fix for each row is one click in EPMS. */}
        {undated && undated.bills > 0 && (
          <div className="mt-6">
            <h3 className="mb-1 flex items-center gap-1.5 text-sm font-semibold text-fg">
              <HelpCircle className="h-4 w-4 text-fg-muted" />
              No payment term on file — {money(undated.amount)} {currency} the forecast cannot place
            </h3>
            <p className="mb-2 max-w-3xl text-2xs leading-relaxed text-fg-muted">
              NC bills these suppliers, but we hold no payment term for them, so there is no
              due date to calculate. They are excluded from both tables above rather than
              defaulted to Net 30.{' '}
              {/* Two different fixes, and which one applies is mostly decided by
                  the date. A single day carrying most of this is an opening-
                  balance load, and setting those suppliers up in the vendor
                  master would be work spent on money that should be judged. */}
              {missing?.largest_single_date
                && Number(missing.largest_single_date.amount) > Number(undated.amount) / 2 ? (
                <>
                  <strong>{missing.largest_single_date.bills} of these bills are all dated{' '}
                  {day(missing.largest_single_date.bill_date)}</strong> and carry{' '}
                  {money(missing.largest_single_date.amount)} — that is an opening-balance load,
                  not suppliers waiting to be set up. Judge those on{' '}
                  <a href="/finance/ap-ledger-health" className="text-primary-700 hover:underline">
                    AP Subledger Health</a> instead. For the rest,{' '}
                  <strong>create the vendor record</strong> (EPMS &rsquo;Vendors&rsquo; → From ERP) or set
                  the term on the one that exists, and the balance moves into the forecast.
                </>
              ) : (
                <>
                  <strong>Create the vendor record</strong> (EPMS &rsquo;Vendors&rsquo; → From ERP), or set
                  the term on the one that exists, and the balance moves into the forecast on
                  the next load.
                </>
              )}
            </p>
            <div className="overflow-x-auto rounded-lg border border-line">
              <Table density="compact" className="min-w-[720px]">
                <THead>
                  <Tr className="bg-surface-subtle text-xs text-fg-muted">
                    <SortTh label="Supplier" sortKey="supplier" sort={missingSort.sort} onSort={missingSort.onSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="What is missing" sortKey="reason" sort={missingSort.sort} onSort={missingSort.onSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="Bills" sortKey="bills" sort={missingSort.sort} onSort={missingSort.onSort} align="right" className="px-3 py-2 font-medium" />
                    <SortTh label="Open balance" sortKey="amount" sort={missingSort.sort} onSort={missingSort.onSort} align="right" className="px-3 py-2 font-medium" />
                    <SortTh label="Bills dated" sortKey="dated" sort={missingSort.sort} onSort={missingSort.onSort} className="px-3 py-2 font-medium text-left" />
                    <Th></Th>
                  </Tr>
                </THead>
                <TBody>
                  {(missing?.items.length ?? 0) === 0 ? (
                    <Tr><Td align="center" colSpan={6} className="text-fg-muted">
                      Loading…
                    </Td></Tr>
                  ) : missingSort.rows.map((r) => (
                    <Tr key={(r.supplier_code ?? '') + r.currency}
                        onClick={() => {
                          if (!r.supplier_code) return
                          const same = supplier?.code === r.supplier_code
                          setBucket(same ? null : UNDATED)
                          setSupplier(same ? null : { code: r.supplier_code, name: r.supplier_name })
                        }}
                        className={cn('border-t border-line-subtle',
                                      r.supplier_code && 'cursor-pointer hover:bg-surface-subtle/60',
                                      supplier?.code === r.supplier_code && 'bg-primary-50/60')}>
                      <Td>
                        <div className="text-fg">{r.supplier_name ?? '—'}</div>
                        <div className="font-mono text-2xs text-fg-muted">{r.supplier_code ?? '—'}</div>
                      </Td>
                      <Td className="text-xs">
                        {r.reason === 'no_vendor_record' ? (
                          <Badge variant="warning" size="sm">
                            no vendor record
                          </Badge>
                        ) : (
                          <Badge size="sm">
                            term &ldquo;{r.payment_terms}&rdquo; not recognised
                          </Badge>
                        )}
                      </Td>
                      <Td className={cell}>{r.bills}</Td>
                      <Td className={cn(cell, 'font-semibold')}>{money(r.amount)}</Td>
                      <Td className="whitespace-nowrap text-xs text-fg-muted">
                        {day(r.oldest_bill)} – {day(r.newest_bill)}
                      </Td>
                      <Td className="text-xs font-medium text-primary-700">
                        {r.supplier_code && 'Show bills'}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
          </div>
        )}

      </div>
    </PortalChromeLayout>
  )
}
