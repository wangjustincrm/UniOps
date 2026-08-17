// Inventory → Lots. Search and browse individual WMS lots.
import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Search, X,
} from 'lucide-react'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  formatDateOnly, inventoryApi, qty,
  type AgingBucketKey, type LotFilters,
} from './inventoryApi'

const PAGE_SIZE = 25

const SORTABLE = [
  { key: 'material_code', label: 'Material' },
  { key: 'lot_no', label: 'Lot' },
  { key: 'qty', label: 'Qty' },
  { key: 'expiry_date', label: 'Expiry' },
  { key: 'inbound_date', label: 'Received' },
] as const

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
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export function LotsTab({ erpClassCode }: { erpClassCode: string }) {
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState<SortKey>('expiry_date')
  const [descending, setDescending] = useState(false)

  // Debounced: the box drives a server-side query, and one request per
  // keystroke would have the table flickering through half-typed answers.
  useEffect(() => {
    const timer = setTimeout(() => { setSearch(searchInput); setPage(1) }, 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  useEffect(() => { setPage(1) }, [erpClassCode, status, sort, descending])

  const filters: LotFilters = useMemo(() => ({
    search: search || undefined,
    mapped_status: status || undefined,
    erp_class_code: erpClassCode || undefined,
    sort,
    descending,
  }), [search, status, erpClassCode, sort, descending])

  const lotsQuery = useQuery({
    queryKey: ['inventory-lots', page, filters],
    queryFn: () => inventoryApi.listLots(page, PAGE_SIZE, filters),
    placeholderData: keepPreviousData,
  })

  const items = lotsQuery.data?.items ?? []
  const total = lotsQuery.data?.total ?? 0
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
            placeholder="Search material, name, lot number or supplier batch…"
            aria-label="Search inventory lots"
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
          {search || status || erpClassCode
            ? `${total.toLocaleString('en-US')} matching lot(s)`
            : `${total.toLocaleString('en-US')} lot(s)`}
        </span>
      </div>

      {lotsQuery.isError && (
        // An empty table and a failed request must never look the same — the
        // single most repeated bug in this project.
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {errMsg(lotsQuery.error, 'Could not load inventory lots.')}
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
              <th className="px-3 py-2 text-left">Shelf life</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Supplier batch</th>
            </tr>
          </thead>
          <tbody>
            {lotsQuery.isLoading && (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-neutral-400">Loading lots…</td></tr>
            )}
            {!lotsQuery.isLoading && items.length === 0 && !lotsQuery.isError && (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-sm text-neutral-400">
                {search
                  ? `Nothing matches "${search}".`
                  : 'No lots for these filters.'}
              </td></tr>
            )}
            {items.map((lot) => (
              <tr key={lot.id} className={cn(
                'border-t border-neutral-100',
                lot.aging_bucket === 'expired' && 'bg-danger-50/40',
              )}>
                <td className="px-3 py-2">
                  <span className="font-mono text-xs">{lot.material_code}</span>
                  {lot.material_name && (
                    <span className="ml-1.5 text-xs text-neutral-500">{lot.material_name}</span>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{lot.lot_no}</td>
                <td className="px-3 py-2 text-right font-mono">{qty(lot.qty)}</td>
                <td className="px-3 py-2 text-xs text-neutral-600">{formatDateOnly(lot.expiry_date)}</td>
                <td className="px-3 py-2 text-xs text-neutral-500">{formatDateOnly(lot.inbound_date)}</td>
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
                  <span className={cn(
                    'rounded-full border px-2 py-0.5 text-[11px] font-medium',
                    STATUS_STYLE[lot.mapped_status] ?? 'bg-neutral-50 text-neutral-600 border-neutral-200',
                  )}>
                    {lot.mapped_status}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono text-[11px] text-neutral-500">
                  {lot.supplier_batch || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-neutral-500">
          {from}–{to} of {total.toLocaleString('en-US')}
          {lotsQuery.data && <> · as of {formatDateOnly(lotsQuery.data.as_of)}</>}
        </span>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || lotsQuery.isFetching} aria-label="Previous page"
            className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-[11px] text-neutral-500">Page {page} of {pageCount}</span>
          <button type="button" onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            disabled={page >= pageCount || lotsQuery.isFetching} aria-label="Next page"
            className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
