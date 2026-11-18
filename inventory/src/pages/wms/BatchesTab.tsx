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
import { SortTh, sortParams, useSort, Input, Select, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  formatDateOnly, inventoryApi, qty,
  type AgingBucketKey, type BatchFilters, type BatchSortKey,
} from './inventoryApi'
import { InventorySummary } from './InventorySummary'
import { ScrollTable, stickyHeadCls } from '@/components/ScrollTable'

const PAGE_SIZE = 25

const thL = 'px-3 py-2 text-left'
const thR = 'px-3 py-2'

// `inbound_date` is deliberately NOT a column here any more: a batch's lots
// arrive on different days, so one date on the summary row describes only part
// of the quantity. It lives on the location rows in the expander, where each
// one belongs to exactly one lot and the date is unambiguous.

/** Tint per shelf-life band. Expired and under-30 are the two that need an
 *  action today, so only those two carry colour — if everything is coloured,
 *  nothing is. */
const BUCKET_STYLE: Record<AgingBucketKey, string> = {
  expired: 'bg-danger-soft text-danger-fg border-danger-line',
  under_30: 'bg-warning-soft text-warning-fg border-warning-line',
  '30_to_60': 'bg-surface-subtle text-fg-secondary border-line',
  '60_to_90': 'bg-surface-subtle text-fg-secondary border-line',
  '90_to_180': 'bg-surface-subtle text-fg-secondary border-line',
  over_180: 'bg-surface-subtle text-fg-muted border-line',
}

const STATUS_STYLE: Record<string, string> = {
  available: 'bg-success-soft text-success-fg border-success-line',
  hold: 'bg-warning-soft text-warning-fg border-warning-line',
  expired: 'bg-danger-soft text-danger-fg border-danger-line',
  mixed: 'bg-surface-sunken text-fg-secondary border-line-strong',
}

/** The WAREHOUSE's own quality status (Flux QLT_STS), shown ALONGSIDE the
 *  derived one rather than instead of it: 278 lots are "Release" in the
 *  warehouse and expired by date, and one column cannot say which is which. */
const QUALITY_STYLE: Record<string, string> = {
  Release: 'bg-success-soft text-success-fg border-success-line',
  Block: 'bg-danger-soft text-danger-fg border-danger-line',
  'Under Inspection': 'bg-warning-soft text-warning-fg border-warning-line',
  Mixed: 'bg-surface-sunken text-fg-secondary border-line-strong',
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
    return <p className="px-3 py-2 text-xs text-fg-muted">Loading locations…</p>
  }
  if (query.isError) {
    return (
      <p role="alert" className="flex items-start gap-1.5 px-3 py-2 text-xs text-danger-fg">
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
      <p className="px-3 py-2 text-xs text-fg-muted">
        No location recorded for this batch.
      </p>
    )
  }

  return (
    <Table density="compact" className="min-w-full">
      <THead>
        <Tr>
          <Th>Location</Th>
          <Th>Zone</Th>
          <Th>Tracking ID</Th>
          <Th align="right">Qty</Th>
          <Th>Produced</Th>
          <Th
            title="Dates before 2025-02-03 were carried over by WMS's opening-stock import — WMS has no movements before that day.">
            Received
          </Th>
          <Th>Expiry</Th>
          <Th>Quality</Th>
        </Tr>
      </THead>
      <TBody>
        {rows.map((row) => (
          <Tr key={`${row.location_id}|${row.trace_id ?? ''}|${row.lot_no}`}
           >
            <Td>
              <span className="inline-flex items-center gap-1 font-mono">
                <MapPin aria-hidden className="h-3 w-3 text-fg-muted" />
                {row.location_id}
              </span>
            </Td>
            <Td className="text-fg-muted">{row.zone_id ?? '—'}</Td>
            <Td className="font-mono text-fg-muted">{row.trace_id ?? '—'}</Td>
            <Td align="right" className="font-mono font-medium">
              {qty(row.qty)}
              {uom && <span className="ml-1 font-sans text-fg-muted">{uom}</span>}
            </Td>
            <Td className="text-fg-secondary">{formatDateOnly(row.production_date)}</Td>
            <Td className="text-fg-secondary">{formatDateOnly(row.inbound_date)}</Td>
            <Td className="text-fg-secondary">{formatDateOnly(row.expiry_date)}</Td>
            <Td className="text-fg-secondary">{row.quality_status_label ?? '—'}</Td>
          </Tr>
        ))}
      </TBody>
    </Table>
  )
}

export function BatchesTab({
  erpClassCode, includeByproducts,
}: {
  erpClassCode: string
  includeByproducts: boolean
}) {
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  // Earliest expiry first is the API's default; shown as the active sort.
  const [sort, onSort] = useSort<BatchSortKey>({ key: 'expiry_date', dir: 'asc' })
  const [expanded, setExpanded] = useState<string | null>(null)
  // Set by clicking the "expiring soon" tile. Held as the exact window the
  // server used rather than as a flag, so the rows shown are the same set the
  // tile counted rather than a window rebuilt here.
  const [expiringWindow, setExpiringWindow] =
    useState<{ after: string; before: string } | null>(null)

  // Debounced: the box drives a server-side query, and one request per
  // keystroke would have the table flickering through half-typed answers.
  useEffect(() => {
    const timer = setTimeout(() => { setSearch(searchInput); setPage(1) }, 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  // Back to page 1 when the filters change — adjusted during render (React's
  // "storing information from previous renders"), not in an effect.
  const resetKey = JSON.stringify([erpClassCode, status, sort, expiringWindow, includeByproducts])
  const [prevResetKey, setPrevResetKey] = useState(resetKey)
  if (prevResetKey !== resetKey) {
    setPrevResetKey(resetKey)
    setPage(1)
  }

  const filters: BatchFilters = useMemo(() => ({
    search: search || undefined,
    mapped_status: status || undefined,
    erp_class_code: erpClassCode || undefined,
    include_byproducts: includeByproducts,
    expiring_after: expiringWindow?.after,
    expiring_before: expiringWindow?.before,
    ...sortParams(sort),
  }), [search, status, erpClassCode, sort, expiringWindow,
       includeByproducts])

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

  return (
    <div className="flex flex-col gap-3">
      <InventorySummary
        summary={batchQuery.data?.summary ?? []}
        asOf={batchQuery.data?.as_of}
        warningDays={batchQuery.data?.expiry_warning_days ?? 90}
        isLoading={batchQuery.isLoading}
        expiringFilterActive={expiringWindow !== null}
        onToggleExpiring={setExpiringWindow}
      />

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex h-10 flex-1 items-center gap-2 rounded-lg border border-line-strong bg-surface px-3 sm:max-w-md">
          <Search aria-hidden className="h-4 w-4 shrink-0 text-fg-muted" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search material, name, supplier batch or NC QC batch…"
            aria-label="Search inventory stock"
            className="w-full"
          />
          {searchInput && (
            <Button variant="subtle" size="icon-xs" type="button" onClick={() => setSearchInput('')} aria-label="Clear search"
              className="shrink-0">
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>

        <label className="flex items-center gap-1.5 text-xs text-fg-secondary">
          Status
          <Select selectSize="sm"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by stock status"
           
          >
            <option value="">Any</option>
            <option value="available">Available</option>
            <option value="hold">On hold</option>
            <option value="expired">Expired</option>
          </Select>
        </label>

        <span className="text-xs text-fg-muted">
          {/* Both numbers, because both are true: somebody who knows the
              warehouse holds 543 lots would otherwise think rows went missing. */}
          {total.toLocaleString('en-US')} batch(es)
          <span className="text-fg-muted"> · {totalLots.toLocaleString('en-US')} lot(s)</span>
        </span>
      </div>

      {batchQuery.isError && (
        // An empty table and a failed request must never look the same — the
        // single most repeated bug in this project.
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-xs text-danger-fg">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {errMsg(batchQuery.error, 'Could not load inventory.')}
        </p>
      )}

      <ScrollTable>
        <Table density="compact" className="min-w-full">
          <THead className={stickyHeadCls}>
            <Tr>
              <SortTh label="Material" sortKey="material_code" sort={sort} onSort={onSort} className={thL} />
              <SortTh label="Supplier batch" sortKey="supplier_batch" sort={sort} onSort={onSort} className={thL} />
              <SortTh label="NC QC batch" sortKey="qc_batches" sort={sort} onSort={onSort} className={thL}
                title="NC QC batch numbers (WMS LOTATT04) of this batch's lots. Filled from the next WMS sync on." />
              <SortTh label="Qty" sortKey="qty" sort={sort} onSort={onSort} align="right" className={thR} />
              {/* Oldest-produced-first is how anybody consuming stock in
                  production order wants to read this. */}
              <SortTh label="Produced" sortKey="production_date" sort={sort} onSort={onSort} className={thL} />
              <SortTh label="Expiry" sortKey="expiry_date" sort={sort} onSort={onSort} className={thL} />
              <SortTh label="Lots" sortKey="lots" sort={sort} onSort={onSort} align="right" className={thR} />
              <SortTh label="Shelf life" sortKey="days_to_expiry" sort={sort} onSort={onSort} className={thL} />
              <SortTh label="Quality" sortKey="quality_status" sort={sort} onSort={onSort} className={thL} />
              <SortTh label="Status" sortKey="mapped_status" sort={sort} onSort={onSort} className={thL} />
            </Tr>
          </THead>
          <TBody>
            {batchQuery.isLoading && (
              <Tr><Td align="center" colSpan={10} className="text-fg-muted">Loading stock…</Td></Tr>
            )}
            {!batchQuery.isLoading && items.length === 0 && !batchQuery.isError && (
              <Tr><Td align="center" colSpan={10} className="text-sm text-fg-muted">
                {search
                  ? `Nothing matches "${search}".`
                  : 'No stock for these filters.'}
              </Td></Tr>
            )}
            {items.map((lot) => {
              const key = `${lot.warehouse_id}|${lot.material_code}|${lot.supplier_batch ?? ''}`
              const isOpen = expanded === key
              return [
              <Tr key={key} className={cn(
                'border-t border-line-subtle',
                lot.aging_bucket === 'expired' && 'bg-danger-50/40',
              )}>
                <Td>
                  <span className="font-mono text-xs">{lot.material_code}</span>
                  {lot.material_name && (
                    <span className="ml-1.5 text-xs text-fg-muted">{lot.material_name}</span>
                  )}
                </Td>
                <Td className="text-xs">
                  <Button variant="link"
                    type="button"
                    onClick={() => setExpanded(isOpen ? null : key)}
                    aria-expanded={isOpen}
                    className="font-mono"
                    title="Show where this batch is stored"
                  >
                    {lot.supplier_batch ?? (
                      // Not blank: some lots genuinely carry no supplier batch,
                      // and an empty cell reads as a rendering fault.
                      <span className="font-sans text-fg-muted">no supplier batch</span>
                    )}
                    {isOpen ? <ChevronUp aria-hidden className="h-3 w-3 shrink-0" />
                            : <ChevronDown aria-hidden className="h-3 w-3 shrink-0" />}
                  </Button>
                </Td>
                <Td className="font-mono text-xs text-fg-secondary">
                  {lot.qc_batches || <span className="font-sans text-fg-muted">—</span>}
                </Td>
                <Td align="right" className="font-mono">
                  {qty(lot.qty)}
                  {lot.base_uom && (
                    <span className="ml-1 font-sans text-xs text-fg-muted">{lot.base_uom}</span>
                  )}
                </Td>
                <Td className="text-xs text-fg-secondary">
                  {formatDateOnly(lot.production_date)}
                  {lot.production_spans_dates && (
                    <span
                      className="ml-1 cursor-help text-fg-muted"
                      title="This batch's lots have different production dates — the earliest is shown"
                    >+</span>
                  )}
                </Td>
                <Td className="text-xs text-fg-secondary">
                  {formatDateOnly(lot.expiry_date)}
                  {lot.expiry_spans_dates && (
                    // The batch's lots do not share one date, so the date shown
                    // describes only part of the quantity. Saying so beats
                    // presenting the earliest as if it were the whole batch.
                    <span
                      className="ml-1 cursor-help text-fg-muted"
                      title="This batch's lots have different expiry dates — the earliest is shown"
                    >+</span>
                  )}
                </Td>
                <Td align="right" className="font-mono text-xs text-fg-muted">{lot.lots}</Td>
                <Td>
                  {lot.aging_bucket === null ? (
                    // Not a band, and not rendered as one. Packaging has no
                    // expiry because it does not expire.
                    <span className="text-xs text-fg-muted" title="This material has no shelf life on file">
                      no expiry
                    </span>
                  ) : (
                    <span className={cn(
                      'rounded-full border px-2 py-0.5 text-2xs font-medium',
                      BUCKET_STYLE[lot.aging_bucket],
                    )}>
                      {lot.days_to_expiry !== null && lot.days_to_expiry < 0
                        ? `${Math.abs(lot.days_to_expiry)} d past`
                        : `${lot.days_to_expiry} d left`}
                    </span>
                  )}
                </Td>
                <Td>
                  {lot.quality_status_label && (
                    <span
                      className={cn(
                        'rounded-full border px-2 py-0.5 text-2xs font-medium',
                        QUALITY_STYLE[lot.quality_status_label]
                          ?? 'bg-surface-subtle text-fg-secondary border-line',
                      )}
                      title={`Warehouse quality status — Flux QLT_STS ${lot.quality_status}`}
                    >
                      {lot.quality_status_label}
                    </span>
                  )}
                </Td>
                <Td>
                  <span
                    className={cn(
                      'rounded-full border px-2 py-0.5 text-2xs font-medium',
                      STATUS_STYLE[lot.mapped_status] ?? 'bg-surface-subtle text-fg-secondary border-line',
                    )}
                    title="Warehouse status with shelf life applied — what planning uses"
                  >
                    {lot.mapped_status}
                  </span>
                </Td>
              </Tr>,
              isOpen && (
                <Tr key={`${key}-locations`} className="bg-surface-subtle/60">
                  <Td colSpan={10}>
                    <BatchLocations
                      materialCode={lot.material_code}
                      supplierBatch={lot.supplier_batch}
                      uom={lot.base_uom}
                    />
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
          {batchQuery.data && <> · expiry checked against {formatDateOnly(batchQuery.data.as_of)}</>}
        </span>
        <div className="flex items-center gap-1">
          <Button variant="subtle" size="icon-sm" type="button" onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || batchQuery.isFetching} aria-label="Previous page"
           >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-2xs text-fg-muted">Page {page} of {pageCount}</span>
          <Button variant="subtle" size="icon-sm" type="button" onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            disabled={page >= pageCount || batchQuery.isFetching} aria-label="Next page"
           >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
