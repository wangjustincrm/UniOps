// Inventory → Batches. Stock grouped by SUPPLIER BATCH.
//
// ★ The WMS lot number is not a column. It is Flux's internal identifier and
// means nothing outside the warehouse system; the plant identifies stock by the
// supplier's batch — what the certificate of analysis carries and what somebody
// quotes on the phone.
//
// Grouping, not merely hiding the column: 2,143 of the plant's 3,532 lots are
// visually identical to another lot once the internal number is gone, and one
// supplier batch can span 192 of them (CP0080, "Old Wooden Racking Pallet").
// Dropping the column alone would have produced pages of repeated rows.
//
// The lot number is still searchable — somebody reading a Flux screen may paste
// one, and finding nothing would be worse than a match they cannot see.
import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, MapPin,
  Search, X,
} from 'lucide-react'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  formatDateOnly, inventoryApi, qty,
  type AgingBucketKey, type BatchFilters,
} from './inventoryApi'

const PAGE_SIZE = 25

const SORTABLE = [
  { key: 'material_code', label: 'Material' },
  { key: 'supplier_batch', label: 'Supplier batch' },
  { key: 'qty', label: 'Qty' },
  // Sortable because oldest-produced-first is how anybody consuming stock in
  // production order wants to read this.
  { key: 'production_date', label: 'Produced' },
  { key: 'expiry_date', label: 'Expiry' },
] as const

// `inbound_date` is deliberately NOT a column here any more: a batch's lots
// arrive on different days, so one date on the summary row describes only part
// of the quantity. It lives on the location rows in the expander, where each
// one belongs to exactly one lot and the date is unambiguous.

type SortKey = typeof SORTABLE[number]['key']

/** Tint per shelf-life band. Expired and under-30 are the two that need an
 *  action today, so only those two carry colour — if everything is coloured,
 *  nothing is. */
const BUCKET_STYLE: Record<AgingBucketKey, string> = {
  expired: 'bg-danger-50 text-danger-700 border-danger-200',
  under_30: 'bg-warning-50 text-warning-800 border-warning-200',
  '30_to_60': 'bg-neutral-50 text-neutral-600 border-neutral-200',
  '60_to_180': 'bg-neutral-50 text-neutral-600 border-neutral-200',
  over_180: 'bg-neutral-50 text-neutral-500 border-neutral-200',
}

const STATUS_STYLE: Record<string, string> = {
  available: 'bg-success-50 text-success-700 border-success-200',
  hold: 'bg-warning-50 text-warning-800 border-warning-200',
  expired: 'bg-danger-50 text-danger-700 border-danger-200',
  mixed: 'bg-neutral-100 text-neutral-700 border-neutral-300',
}

/** The WAREHOUSE's own quality status (Flux QLT_STS), shown ALONGSIDE the
 *  derived one rather than instead of it: 278 lots are "Release" in the
 *  warehouse and expired by date, and one column cannot say which is which. */
const QUALITY_STYLE: Record<string, string> = {
  Release: 'bg-success-50 text-success-700 border-success-200',
  Block: 'bg-danger-50 text-danger-700 border-danger-200',
  'Under Inspection': 'bg-warning-50 text-warning-800 border-warning-200',
  Mixed: 'bg-neutral-100 text-neutral-700 border-neutral-300',
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

/** Where one batch physically sits — the expander under a batch row.
 *
 *  The WMS lot number is what the warehouse system calls a pile of stock; the
 *  LOCATION is what somebody standing in the warehouse needs. A lot can occupy
 *  several (one packaging lot is spread over 28), so this is a genuine second
 *  level rather than a column that would not have fitted. */
function BatchLocations({
  materialCode, supplierBatch, uom,
}: {
  materialCode: string
  supplierBatch: string | null
  uom: string | null
}) {
  const query = useQuery({
    queryKey: ['batch-locations', materialCode, supplierBatch],
    queryFn: () => inventoryApi.batchLocations(materialCode, supplierBatch),
  })

  if (query.isLoading) {
    return <p className="px-3 py-2 text-xs text-neutral-400">Loading locations…</p>
  }
  if (query.isError) {
    return (
      <p role="alert" className="flex items-start gap-1.5 px-3 py-2 text-xs text-danger-700">
        <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {errMsg(query.error, 'Could not load where this batch is stored.')}
      </p>
    )
  }
  const rows = query.data ?? []
  if (rows.length === 0) {
    // Distinct from an error, and distinct from zero stock: the batch exists,
    // the warehouse just has not told us where it is.
    return (
      <p className="px-3 py-2 text-xs text-neutral-400">
        No location recorded for this batch.
      </p>
    )
  }

  return (
    <table className="min-w-full text-xs">
      <thead className="text-[11px] uppercase tracking-wide text-neutral-400">
        <tr>
          <th className="px-3 py-1.5 text-left">Location</th>
          <th className="px-3 py-1.5 text-left">Zone</th>
          <th className="px-3 py-1.5 text-left">Tracking ID</th>
          <th className="px-3 py-1.5 text-right">Qty</th>
          <th className="px-3 py-1.5 text-left">Produced</th>
          <th className="px-3 py-1.5 text-left">Received</th>
          <th className="px-3 py-1.5 text-left">Expiry</th>
          <th className="px-3 py-1.5 text-left">Quality</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={`${row.location_id}|${row.trace_id ?? ''}|${row.lot_no}`}
            className="border-t border-neutral-100">
            <td className="px-3 py-1.5">
              <span className="inline-flex items-center gap-1 font-mono">
                <MapPin aria-hidden className="h-3 w-3 text-neutral-400" />
                {row.location_id}
              </span>
            </td>
            <td className="px-3 py-1.5 text-neutral-500">{row.zone_id ?? '—'}</td>
            <td className="px-3 py-1.5 font-mono text-neutral-500">{row.trace_id ?? '—'}</td>
            <td className="px-3 py-1.5 text-right font-mono font-medium">
              {qty(row.qty)}
              {uom && <span className="ml-1 font-sans text-neutral-400">{uom}</span>}
            </td>
            <td className="px-3 py-1.5 text-neutral-600">{formatDateOnly(row.production_date)}</td>
            <td className="px-3 py-1.5 text-neutral-600">{formatDateOnly(row.inbound_date)}</td>
            <td className="px-3 py-1.5 text-neutral-600">{formatDateOnly(row.expiry_date)}</td>
            <td className="px-3 py-1.5 text-neutral-600">{row.quality_status_label ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function BatchesTab({ erpClassCode }: { erpClassCode: string }) {
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState<SortKey>('expiry_date')
  const [descending, setDescending] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)

  // Debounced: the box drives a server-side query, and one request per
  // keystroke would have the table flickering through half-typed answers.
  useEffect(() => {
    const timer = setTimeout(() => { setSearch(searchInput); setPage(1) }, 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  useEffect(() => { setPage(1) }, [erpClassCode, status, sort, descending])

  const filters: BatchFilters = useMemo(() => ({
    search: search || undefined,
    mapped_status: status || undefined,
    erp_class_code: erpClassCode || undefined,
    sort,
    descending,
  }), [search, status, erpClassCode, sort, descending])

  const batchQuery = useQuery({
    queryKey: ['inventory-batches', page, filters],
    queryFn: () => inventoryApi.listBatches(page, PAGE_SIZE, filters),
    placeholderData: keepPreviousData,
  })

  const items = batchQuery.data?.items ?? []
  const total = batchQuery.data?.total ?? 0
  const totalLots = batchQuery.data?.total_lots ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const from = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const to = Math.min(page * PAGE_SIZE, total)

  function toggleSort(key: SortKey) {
    if (key === sort) setDescending((d) => !d)
    else { setSort(key); setDescending(key === 'qty') }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex h-10 flex-1 items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3 sm:max-w-md">
          <Search aria-hidden className="h-4 w-4 shrink-0 text-neutral-400" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search material, name or supplier batch…"
            aria-label="Search inventory stock"
            className="w-full text-sm focus:outline-none"
          />
          {searchInput && (
            <button type="button" onClick={() => setSearchInput('')} aria-label="Clear search"
              className="shrink-0 text-neutral-400 hover:text-neutral-700">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <label className="flex items-center gap-1.5 text-xs text-neutral-600">
          Status
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by stock status"
            className="h-9 rounded-lg border border-neutral-300 bg-white px-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="">Any</option>
            <option value="available">Available</option>
            <option value="hold">On hold</option>
            <option value="expired">Expired</option>
          </select>
        </label>

        <span className="text-xs text-neutral-500">
          {/* Both numbers, because both are true: somebody who knows the
              warehouse holds 543 lots would otherwise think rows went missing. */}
          {total.toLocaleString('en-US')} batch(es)
          <span className="text-neutral-400"> · {totalLots.toLocaleString('en-US')} lot(s)</span>
        </span>
      </div>

      {batchQuery.isError && (
        // An empty table and a failed request must never look the same — the
        // single most repeated bug in this project.
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {errMsg(batchQuery.error, 'Could not load inventory.')}
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500">
            <tr>
              {SORTABLE.map(({ key, label }) => (
                <th key={key} className={cn('px-3 py-2', key === 'qty' ? 'text-right' : 'text-left')}>
                  <button
                    type="button"
                    onClick={() => toggleSort(key)}
                    aria-label={`Sort by ${label}`}
                    className={cn('inline-flex items-center gap-1 hover:text-neutral-800',
                      sort === key && 'text-neutral-900')}
                  >
                    {label}
                    {sort === key && (descending
                      ? <ChevronDown aria-hidden className="h-3 w-3" />
                      : <ChevronUp aria-hidden className="h-3 w-3" />)}
                  </button>
                </th>
              ))}
              <th className="px-3 py-2 text-right">Lots</th>
              <th className="px-3 py-2 text-left">Shelf life</th>
              <th className="px-3 py-2 text-left">Quality</th>
              <th className="px-3 py-2 text-left">Status</th>
            </tr>
          </thead>
          <tbody>
            {batchQuery.isLoading && (
              <tr><td colSpan={9} className="px-3 py-8 text-center text-neutral-400">Loading stock…</td></tr>
            )}
            {!batchQuery.isLoading && items.length === 0 && !batchQuery.isError && (
              <tr><td colSpan={9} className="px-3 py-8 text-center text-sm text-neutral-400">
                {search
                  ? `Nothing matches "${search}".`
                  : 'No stock for these filters.'}
              </td></tr>
            )}
            {items.map((lot) => {
              const key = `${lot.warehouse_id}|${lot.material_code}|${lot.supplier_batch ?? ''}`
              const isOpen = expanded === key
              return [
              <tr key={key} className={cn(
                'border-t border-neutral-100',
                lot.aging_bucket === 'expired' && 'bg-danger-50/40',
              )}>
                <td className="px-3 py-2">
                  <span className="font-mono text-xs">{lot.material_code}</span>
                  {lot.material_name && (
                    <span className="ml-1.5 text-xs text-neutral-500">{lot.material_name}</span>
                  )}
                </td>
                <td className="px-3 py-2 text-xs">
                  <button
                    type="button"
                    onClick={() => setExpanded(isOpen ? null : key)}
                    aria-expanded={isOpen}
                    className="inline-flex items-center gap-1 font-mono hover:text-primary-700 hover:underline"
                    title="Show where this batch is stored"
                  >
                    {lot.supplier_batch ?? (
                      // Not blank: some lots genuinely carry no supplier batch,
                      // and an empty cell reads as a rendering fault.
                      <span className="font-sans text-neutral-400">no supplier batch</span>
                    )}
                    {isOpen ? <ChevronUp aria-hidden className="h-3 w-3 shrink-0" />
                            : <ChevronDown aria-hidden className="h-3 w-3 shrink-0" />}
                  </button>
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {qty(lot.qty)}
                  {lot.base_uom && (
                    <span className="ml-1 font-sans text-xs text-neutral-400">{lot.base_uom}</span>
                  )}
                </td>
                <td className="px-3 py-2 text-xs text-neutral-600">
                  {formatDateOnly(lot.production_date)}
                  {lot.production_spans_dates && (
                    <span
                      className="ml-1 cursor-help text-neutral-400"
                      title="This batch's lots have different production dates — the earliest is shown"
                    >+</span>
                  )}
                </td>
                <td className="px-3 py-2 text-xs text-neutral-600">
                  {formatDateOnly(lot.expiry_date)}
                  {lot.expiry_spans_dates && (
                    // The batch's lots do not share one date, so the date shown
                    // describes only part of the quantity. Saying so beats
                    // presenting the earliest as if it were the whole batch.
                    <span
                      className="ml-1 cursor-help text-neutral-400"
                      title="This batch's lots have different expiry dates — the earliest is shown"
                    >+</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs text-neutral-500">{lot.lots}</td>
                <td className="px-3 py-2">
                  {lot.aging_bucket === null ? (
                    // Not a band, and not rendered as one. Packaging has no
                    // expiry because it does not expire.
                    <span className="text-xs text-neutral-400" title="This material has no shelf life on file">
                      no expiry
                    </span>
                  ) : (
                    <span className={cn(
                      'rounded-full border px-2 py-0.5 text-[11px] font-medium',
                      BUCKET_STYLE[lot.aging_bucket],
                    )}>
                      {lot.days_to_expiry !== null && lot.days_to_expiry < 0
                        ? `${Math.abs(lot.days_to_expiry)} d past`
                        : `${lot.days_to_expiry} d left`}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">
                  {lot.quality_status_label && (
                    <span
                      className={cn(
                        'rounded-full border px-2 py-0.5 text-[11px] font-medium',
                        QUALITY_STYLE[lot.quality_status_label]
                          ?? 'bg-neutral-50 text-neutral-600 border-neutral-200',
                      )}
                      title={`Warehouse quality status — Flux QLT_STS ${lot.quality_status}`}
                    >
                      {lot.quality_status_label}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={cn(
                      'rounded-full border px-2 py-0.5 text-[11px] font-medium',
                      STATUS_STYLE[lot.mapped_status] ?? 'bg-neutral-50 text-neutral-600 border-neutral-200',
                    )}
                    title="Warehouse status with shelf life applied — what planning uses"
                  >
                    {lot.mapped_status}
                  </span>
                </td>
              </tr>,
              isOpen && (
                <tr key={`${key}-locations`} className="border-t border-neutral-100 bg-neutral-50/60">
                  <td colSpan={9} className="p-0">
                    <BatchLocations
                      materialCode={lot.material_code}
                      supplierBatch={lot.supplier_batch}
                      uom={lot.base_uom}
                    />
                  </td>
                </tr>
              ),
              ]
            })}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-neutral-500">
          {from}–{to} of {total.toLocaleString('en-US')}
          {batchQuery.data && <> · as of {formatDateOnly(batchQuery.data.as_of)}</>}
        </span>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || batchQuery.isFetching} aria-label="Previous page"
            className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-[11px] text-neutral-500">Page {page} of {pageCount}</span>
          <button type="button" onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            disabled={page >= pageCount || batchQuery.isFetching} aria-label="Next page"
            className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
