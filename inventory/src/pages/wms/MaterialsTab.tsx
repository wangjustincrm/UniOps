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
import { SortTh, sortParams, useSort, Checkbox, Input, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatDateOnly, inventoryApi, isPositiveQty, qty, type MaterialSortKey } from './inventoryApi'
import { isZero } from '@/lib/number'
import { ScrollTable, stickyHeadCls } from '@/components/ScrollTable'

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
    return <p className="px-3 py-2 text-xs text-fg-muted">Loading purchase orders…</p>
  }
  if (query.isError) {
    return (
      <p role="alert" className="flex items-start gap-1.5 px-3 py-2 text-xs text-danger-fg">
        <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {errMsg(query.error, 'Could not load the purchase orders behind this figure.')}
      </p>
    )
  }
  const lines = query.data ?? []
  if (lines.length === 0) {
    return <p className="px-3 py-2 text-xs text-fg-muted">Nothing on order.</p>
  }

  return (
    <Table density="compact" className="min-w-full">
      <THead>
        <Tr>
          <Th>PO</Th>
          <Th>Supplier</Th>
          <Th align="right">Ordered</Th>
          <Th align="right">Received</Th>
          <Th align="right">Still owed</Th>
          <Th>Expected</Th>
        </Tr>
      </THead>
      <TBody>
        {lines.map((line, i) => (
          <Tr key={`${line.po_number}-${i}`}>
            <Td className="font-mono">{line.po_number}</Td>
            <Td className="text-fg-secondary">{line.vendor_name}</Td>
            <Td align="right" className="font-mono text-fg-muted">
              {qty(line.ordered)} {line.unit}
            </Td>
            <Td align="right" className="font-mono text-fg-muted">
              {qty(line.received)}
            </Td>
            <Td align="right" className="font-mono font-medium">
              {qty(line.remaining)}
            </Td>
            <Td>
              {line.expected_arrival ? (
                <span className="text-fg-secondary">
                  {formatDateOnly(line.expected_arrival)}
                  {line.arrival_is_from_header && (
                    // Whose date it is. The ERP's line date is authoritative;
                    // the header one was typed by a person.
                    <span className="ml-1 text-fg-muted" title="Entered on the purchase order, not supplied by the ERP">
                      (entered)
                    </span>
                  )}
                </span>
              ) : (
                // Not blank: an empty cell in a date column reads as "none",
                // and "we were never told" is a different and actionable fact.
                <span className="text-fg-muted" title="Neither the ERP nor the purchase order states an arrival date">
                  not stated
                </span>
              )}
            </Td>
          </Tr>
        ))}
      </TBody>
    </Table>
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
  const [sort, onSort] = useSort<MaterialSortKey>()

  useEffect(() => {
    const timer = setTimeout(() => { setSearch(searchInput); setPage(1) }, 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  // Back to page 1 when the filters change — adjusted during render (React's
  // "storing information from previous renders"), not in an effect.
  const resetKey = JSON.stringify([erpClassCode, onlyWithStock, includeByproducts, sort])
  const [prevResetKey, setPrevResetKey] = useState(resetKey)
  if (prevResetKey !== resetKey) {
    setPrevResetKey(resetKey)
    setPage(1)
    setExpanded(null)
  }

  const filters = useMemo(() => ({
    search: search || undefined,
    erp_class_code: erpClassCode || undefined,
    only_with_stock: onlyWithStock,
    include_byproducts: includeByproducts,
    ...sortParams(sort),
  }), [search, erpClassCode, onlyWithStock, includeByproducts, sort])

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
        <div className="flex h-10 flex-1 items-center gap-2 rounded-lg border border-line-strong bg-surface px-3 sm:max-w-md">
          <Search aria-hidden className="h-4 w-4 shrink-0 text-fg-muted" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search material code or name…"
            aria-label="Search materials"
            className="w-full"
          />
          {searchInput && (
            <Button variant="subtle" size="icon-xs" type="button" onClick={() => setSearchInput('')} aria-label="Clear search"
              className="shrink-0">
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>

        <label className="flex items-center gap-2 text-xs text-fg-secondary">
          <Checkbox checked={onlyWithStock}
            onChange={(e) => setOnlyWithStock(e.target.checked)}
            />
          Hide materials with no stock and nothing on order
        </label>

        <span className="text-xs text-fg-muted">
          {total.toLocaleString('en-US')} material(s)
        </span>
      </div>

      {query.isError && (
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-xs text-danger-fg">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {errMsg(query.error, 'Could not load material stock.')}
        </p>
      )}

      <ScrollTable>
        <Table density="compact" className="min-w-full">
          <THead className={stickyHeadCls}>
            <Tr>
              <SortTh label="Material" sortKey="material_code" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
              <SortTh label="On hand" sortKey="on_hand" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="Available" sortKey="available" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="On hold" sortKey="on_hold" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="Expired" sortKey="expired_qty" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="Next expiry" sortKey="next_expiry" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
              <SortTh label="On order" sortKey="in_transit" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
              <SortTh label="Arriving" sortKey="earliest_arrival" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
            </Tr>
          </THead>
          <TBody>
            {query.isLoading && (
              <Tr><Td align="center" colSpan={8} className="text-fg-muted">Loading materials…</Td></Tr>
            )}
            {!query.isLoading && items.length === 0 && !query.isError && (
              <Tr><Td align="center" colSpan={8} className="text-sm text-fg-muted">
                {search ? `Nothing matches "${search}".` : 'No materials for these filters.'}
              </Td></Tr>
            )}
            {items.map((row) => {
              const noneOnOrder = isZero(row.in_transit)
              const isOpen = expanded === row.material_code
              return [
                <Tr key={row.material_code}>
                  <Td>
                    <span className="font-mono text-xs">{row.material_code}</span>
                    {row.material_name && (
                      <span className="ml-1.5 text-xs text-fg-muted">{row.material_name}</span>
                    )}
                    {row.base_uom && (
                      <span className="ml-1.5 text-2xs text-fg-muted">{row.base_uom}</span>
                    )}
                  </Td>
                  <Td align="right" className="font-mono">{qty(row.on_hand)}</Td>
                  <Td align="right" className="font-mono text-success-fg">{qty(row.available)}</Td>
                  <Td align="right" className="font-mono text-fg-muted">{qty(row.on_hold)}</Td>
                  <Td className={cn('px-3 py-2 text-right font-mono',
                    isPositiveQty(row.expired_qty) ? 'text-danger-fg' : 'text-fg-muted')}>
                    {qty(row.expired_qty)}
                  </Td>
                  <Td className="text-xs">
                    {row.next_expiry ? (
                      <span className={cn(
                        row.days_to_next_expiry !== null && row.days_to_next_expiry < 0 && 'text-danger-fg',
                        row.days_to_next_expiry !== null && row.days_to_next_expiry >= 0
                          && row.days_to_next_expiry < 30 && 'text-warning-fg',
                      )}>
                        {formatDateOnly(row.next_expiry)}
                        {row.days_to_next_expiry !== null && (
                          <span className="ml-1 text-fg-muted">({row.days_to_next_expiry} d)</span>
                        )}
                      </span>
                    ) : <span className="text-fg-muted">—</span>}
                  </Td>
                  <Td align="right">
                    {noneOnOrder ? (
                      // 0, not blank: "nothing is on order" is a fact, and an
                      // empty cell reads as "unknown".
                      <span className="font-mono text-fg-muted">0</span>
                    ) : (
                      <Button variant="link"
                        type="button"
                        onClick={() => setExpanded(isOpen ? null : row.material_code)}
                        aria-expanded={isOpen}
                        className="font-mono"
                      >
                        {qty(row.in_transit)}
                        <span className="text-2xs font-normal text-fg-muted">
                          ({row.open_po_lines} PO)
                        </span>
                        {isOpen ? <ChevronUp aria-hidden className="h-3 w-3" />
                                : <ChevronDown aria-hidden className="h-3 w-3" />}
                      </Button>
                    )}
                  </Td>
                  <Td className="text-xs">
                    {noneOnOrder
                      ? <span className="text-fg-faint">—</span>
                      : row.earliest_arrival
                        ? formatDateOnly(row.earliest_arrival)
                        : <span className="text-fg-muted" title="Neither the ERP nor the purchase order states an arrival date">
                            not stated
                          </span>}
                  </Td>
                </Tr>,
                isOpen && (
                  <Tr key={`${row.material_code}-lines`} className="bg-surface-subtle/60">
                    <Td colSpan={8}>
                      <OpenPoLines materialCode={row.material_code} />
                    </Td>
                  </Tr>
                ),
              ]
            })}
          </TBody>
        </Table>
      </ScrollTable>

      <div className="flex items-center justify-between gap-2">
        <span className="text-2xs text-fg-muted">
          {from}–{to} of {total.toLocaleString('en-US')}
          {query.data && <> · expiry checked against {formatDateOnly(query.data.as_of)}</>}
        </span>
        <div className="flex items-center gap-1">
          <Button variant="subtle" size="icon-sm" type="button" onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || query.isFetching} aria-label="Previous page"
           >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-2xs text-fg-muted">Page {page} of {pageCount}</span>
          <Button variant="subtle" size="icon-sm" type="button" onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            disabled={page >= pageCount || query.isFetching} aria-label="Next page"
           >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
