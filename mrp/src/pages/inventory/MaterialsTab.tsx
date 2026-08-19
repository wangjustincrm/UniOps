// Inventory → Materials. One row per material: what is here, what is coming.
//
// The row a planner actually wants is the one with nothing in the warehouse and
// a delivery on the way, so this list is built from the union of stock and open
// orders rather than from either alone.
//
// The on-order figure expands into the PO lines behind it. A number nobody can
// take apart is a number nobody trusts, and "600 kg on order" raises exactly one
// question: from whom, and when.
import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Search, X,
} from 'lucide-react'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatDateOnly, inventoryApi, qty } from './inventoryApi'

const PAGE_SIZE = 25

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

/** The PO lines behind one material's on-order figure. */
function OpenPoLines({ materialCode }: { materialCode: string }) {
  const query = useQuery({
    queryKey: ['inventory-open-po-lines', materialCode],
    queryFn: () => inventoryApi.openPoLines(materialCode),
  })

  if (query.isLoading) {
    return <p className="px-3 py-2 text-xs text-neutral-400">Loading purchase orders…</p>
  }
  if (query.isError) {
    return (
      <p role="alert" className="flex items-start gap-1.5 px-3 py-2 text-xs text-danger-700">
        <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {errMsg(query.error, 'Could not load the purchase orders behind this figure.')}
      </p>
    )
  }
  const lines = query.data ?? []
  if (lines.length === 0) {
    return <p className="px-3 py-2 text-xs text-neutral-400">Nothing on order.</p>
  }

  return (
    <table className="min-w-full text-xs">
      <thead className="text-[11px] uppercase tracking-wide text-neutral-400">
        <tr>
          <th className="px-3 py-1.5 text-left">PO</th>
          <th className="px-3 py-1.5 text-left">Supplier</th>
          <th className="px-3 py-1.5 text-right">Ordered</th>
          <th className="px-3 py-1.5 text-right">Received</th>
          <th className="px-3 py-1.5 text-right">Still owed</th>
          <th className="px-3 py-1.5 text-left">Expected</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((line, i) => (
          <tr key={`${line.po_number}-${i}`} className="border-t border-neutral-100">
            <td className="px-3 py-1.5 font-mono">{line.po_number}</td>
            <td className="px-3 py-1.5 text-neutral-600">{line.vendor_name}</td>
            <td className="px-3 py-1.5 text-right font-mono text-neutral-500">
              {qty(line.ordered)} {line.unit}
            </td>
            <td className="px-3 py-1.5 text-right font-mono text-neutral-500">
              {qty(line.received)}
            </td>
            <td className="px-3 py-1.5 text-right font-mono font-medium">
              {qty(line.remaining)}
            </td>
            <td className="px-3 py-1.5">
              {line.expected_arrival ? (
                <span className="text-neutral-700">
                  {formatDateOnly(line.expected_arrival)}
                  {line.arrival_is_from_header && (
                    // Whose date it is. The ERP's line date is authoritative;
                    // the header one was typed by a person.
                    <span className="ml-1 text-neutral-400" title="Entered on the purchase order, not supplied by the ERP">
                      (entered)
                    </span>
                  )}
                </span>
              ) : (
                // Not blank: an empty cell in a date column reads as "none",
                // and "we were never told" is a different and actionable fact.
                <span className="text-neutral-400" title="Neither the ERP nor the purchase order states an arrival date">
                  not stated
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function MaterialsTab({
  erpClassCode, includeByproducts,
}: {
  erpClassCode: string
  includeByproducts: boolean
}) {
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [onlyWithStock, setOnlyWithStock] = useState(false)
  const [page, setPage] = useState(1)
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => { setSearch(searchInput); setPage(1) }, 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  useEffect(() => {
    setPage(1); setExpanded(null)
  }, [erpClassCode, onlyWithStock, includeByproducts])

  const filters = useMemo(() => ({
    search: search || undefined,
    erp_class_code: erpClassCode || undefined,
    only_with_stock: onlyWithStock,
    include_byproducts: includeByproducts,
  }), [search, erpClassCode, onlyWithStock, includeByproducts])

  const query = useQuery({
    queryKey: ['inventory-materials', page, filters],
    queryFn: () => inventoryApi.listMaterials(page, PAGE_SIZE, filters),
    placeholderData: keepPreviousData,
  })

  const items = query.data?.items ?? []
  const total = query.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const from = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const to = Math.min(page * PAGE_SIZE, total)

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex h-10 flex-1 items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3 sm:max-w-md">
          <Search aria-hidden className="h-4 w-4 shrink-0 text-neutral-400" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search material code or name…"
            aria-label="Search materials"
            className="w-full text-sm focus:outline-none"
          />
          {searchInput && (
            <button type="button" onClick={() => setSearchInput('')} aria-label="Clear search"
              className="shrink-0 text-neutral-400 hover:text-neutral-700">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <label className="flex items-center gap-2 text-xs text-neutral-600">
          <input type="checkbox" checked={onlyWithStock}
            onChange={(e) => setOnlyWithStock(e.target.checked)}
            className="h-4 w-4 rounded border-neutral-300" />
          Hide materials with no stock and nothing on order
        </label>

        <span className="text-xs text-neutral-500">
          {total.toLocaleString('en-US')} material(s)
        </span>
      </div>

      {query.isError && (
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {errMsg(query.error, 'Could not load material stock.')}
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500">
            <tr>
              <th className="px-3 py-2 text-left">Material</th>
              <th className="px-3 py-2 text-right">On hand</th>
              <th className="px-3 py-2 text-right">Available</th>
              <th className="px-3 py-2 text-right">On hold</th>
              <th className="px-3 py-2 text-right">Expired</th>
              <th className="px-3 py-2 text-left">Next expiry</th>
              <th className="px-3 py-2 text-right">On order</th>
              <th className="px-3 py-2 text-left">Arriving</th>
            </tr>
          </thead>
          <tbody>
            {query.isLoading && (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-neutral-400">Loading materials…</td></tr>
            )}
            {!query.isLoading && items.length === 0 && !query.isError && (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-sm text-neutral-400">
                {search ? `Nothing matches "${search}".` : 'No materials for these filters.'}
              </td></tr>
            )}
            {items.map((row) => {
              const onOrder = Number(row.in_transit)
              const isOpen = expanded === row.material_code
              return [
                <tr key={row.material_code} className="border-t border-neutral-100">
                  <td className="px-3 py-2">
                    <span className="font-mono text-xs">{row.material_code}</span>
                    {row.material_name && (
                      <span className="ml-1.5 text-xs text-neutral-500">{row.material_name}</span>
                    )}
                    {row.base_uom && (
                      <span className="ml-1.5 text-[11px] text-neutral-400">{row.base_uom}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{qty(row.on_hand)}</td>
                  <td className="px-3 py-2 text-right font-mono text-success-700">{qty(row.available)}</td>
                  <td className="px-3 py-2 text-right font-mono text-neutral-500">{qty(row.on_hold)}</td>
                  <td className={cn('px-3 py-2 text-right font-mono',
                    Number(row.expired_qty) > 0 ? 'text-danger-600' : 'text-neutral-400')}>
                    {qty(row.expired_qty)}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {row.next_expiry ? (
                      <span className={cn(
                        row.days_to_next_expiry !== null && row.days_to_next_expiry < 0 && 'text-danger-600',
                        row.days_to_next_expiry !== null && row.days_to_next_expiry >= 0
                          && row.days_to_next_expiry < 30 && 'text-warning-700',
                      )}>
                        {formatDateOnly(row.next_expiry)}
                        {row.days_to_next_expiry !== null && (
                          <span className="ml-1 text-neutral-400">({row.days_to_next_expiry} d)</span>
                        )}
                      </span>
                    ) : <span className="text-neutral-400">—</span>}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {onOrder === 0 ? (
                      // 0, not blank: "nothing is on order" is a fact, and an
                      // empty cell reads as "unknown".
                      <span className="font-mono text-neutral-400">0</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setExpanded(isOpen ? null : row.material_code)}
                        aria-expanded={isOpen}
                        className="inline-flex items-center gap-1 font-mono font-medium text-primary-700 hover:underline"
                      >
                        {qty(row.in_transit)}
                        <span className="text-[11px] font-normal text-neutral-500">
                          ({row.open_po_lines} PO)
                        </span>
                        {isOpen ? <ChevronUp aria-hidden className="h-3 w-3" />
                                : <ChevronDown aria-hidden className="h-3 w-3" />}
                      </button>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {onOrder === 0
                      ? <span className="text-neutral-300">—</span>
                      : row.earliest_arrival
                        ? formatDateOnly(row.earliest_arrival)
                        : <span className="text-neutral-400" title="Neither the ERP nor the purchase order states an arrival date">
                            not stated
                          </span>}
                  </td>
                </tr>,
                isOpen && (
                  <tr key={`${row.material_code}-lines`} className="border-t border-neutral-100 bg-neutral-50/60">
                    <td colSpan={8} className="p-0">
                      <OpenPoLines materialCode={row.material_code} />
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
          {query.data && <> · expiry checked against {formatDateOnly(query.data.as_of)}</>}
        </span>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || query.isFetching} aria-label="Previous page"
            className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-[11px] text-neutral-500">Page {page} of {pageCount}</span>
          <button type="button" onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            disabled={page >= pageCount || query.isFetching} aria-label="Next page"
            className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
