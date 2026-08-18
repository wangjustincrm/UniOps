// Typed client for mrp-api's /inventory/* reads (app/api/v1/inventory.py).
//
// Decimal fields arrive as JSON strings — a project-wide FastAPI/Pydantic
// behaviour — so every quantity below is typed `string` at the wire boundary
// and converted with Number() at the point of display, never trusted as a
// number here.
//
// Every list call takes its filters as an object and builds the query with
// URLSearchParams: filtering and paging both happen server-side, so a filter
// that silently fails to reach the API looks exactly like a filter that
// matched everything.
import { api } from '@/lib/api'

export type AgingBucketKey = 'expired' | 'under_30' | '30_to_60' | '60_to_90'
  | '90_to_180' | 'over_180'

/** Order matters: most urgent first, and the screen renders them in this
 *  order without re-sorting. Mirrors AGING_BUCKETS in
 *  mrp-api/app/services/inventory_aging.py. */
export const AGING_BUCKET_ORDER: AgingBucketKey[] = [
  'expired', 'under_30', '30_to_60', '60_to_90', '90_to_180', 'over_180',
]

/** One supplier batch of one material in one warehouse — the unit this screen
 *  works in.
 *
 *  ★ There is deliberately no `lot_no`. Flux's internal lot number means
 *  nothing outside the warehouse system, and the plant identifies stock by the
 *  supplier's batch: what the certificate of analysis carries and what people
 *  quote on the phone. It is still SEARCHABLE (somebody reading a Flux screen
 *  may paste one) — just never displayed. */
export interface InventoryBatch {
  warehouse_id: string
  material_code: string
  material_name: string | null
  /** The material's own unit. Quantities are meaningless without it: 1,000 of
   *  a raw ingredient is kilograms, 1,000 of a can is pieces. */
  base_uom: string | null
  /** Null for stock the warehouse recorded without one — 92 lots today. */
  supplier_batch: string | null
  qty: string
  qty_allocated: string
  qty_onhold: string
  /** How many WMS lots make up this batch. One can hold a great many: CP0080's
   *  "Old Wooden Racking Pallet" is 192. */
  lots: number
  /** The EARLIEST expiry among the batch's lots — when it starts going out of
   *  date, which is the date somebody acts on. */
  expiry_date: string | null
  /** True when the batch's lots do not share one expiry date (42 of 877).
   *  Shown rather than averaged away: the single date above would otherwise
   *  quietly describe only part of the quantity. */
  expiry_spans_dates: boolean
  inbound_date: string | null
  days_to_expiry: number | null
  aging_bucket: AgingBucketKey | null
  /** When the batch was produced — the earliest among its lots. */
  production_date: string | null
  /** True when the batch's lots do not share one production date (29 of 877). */
  production_spans_dates: boolean
  /** The WAREHOUSE's quality status: Flux QLT_STS 02 Release / 01 Block /
   *  04 Under Inspection, or 'mixed'. ★ Distinct from `mapped_status`, which
   *  folds expiry in on top — 278 lots are Release in the warehouse and
   *  expired by date, and one column cannot say which is which. */
  quality_status: string | null
  quality_status_label: string | null
  /** available | hold | expired | mixed — the warehouse status with expiry
   *  applied, which is what planning consumes. */
  mapped_status: string
  supplier_code: string | null
}

/** One place a batch physically sits.
 *
 *  The grain is (location, handling unit): STAGECANADA holds 15 pallets of one
 *  lot, 700 each, told apart by nothing but their trace id, so collapsing to
 *  location alone would report one 10,500 pallet that does not exist. */
export interface BatchLocation {
  location_id: string
  /** The warehouse's zone for this location — the only human-meaningful thing
   *  its master carries (there is no name or description column). */
  zone_id: string | null
  /** The handling unit. Null where the warehouse recorded none. */
  trace_id: string | null
  qty: string
  qty_allocated: string
  qty_onhold: string
  /** These belong to the LOT in this location, which is why they live here
   *  rather than on the batch row: a batch spanning two production runs has
   *  two answers and a summary row can only show one. */
  production_date: string | null
  inbound_date: string | null
  expiry_date: string | null
  days_to_expiry: number | null
  quality_status: string | null
  quality_status_label: string | null
  mapped_status: string
  /** For tracing a row back into Flux when somebody has to go look at the
   *  physical pallet. Not displayed. */
  lot_no: string
}

/** Totals for one unit of measure over everything the filters match.
 *
 *  ★ One line PER UNIT. The warehouse holds kilograms, pieces, each, rolls and
 *  centipoise, and packaging alone spans five of them — adding them produces a
 *  number that describes nothing. Raw ingredients are all KGM, so the default
 *  view is a single line.
 *
 *  The figures OVERLAP and are not a partition of the total: expired stock is
 *  usually blocked too, and stock expiring soon is still available today. */
export interface InventorySummaryLine {
  uom: string | null
  total_qty: string
  available_qty: string
  expired_qty: string
  /** The WAREHOUSE's block (QLT_STS 01), not our derived hold — material under
   *  inspection is not blocked. */
  blocked_qty: string
  expiring_soon_qty: string
  expiring_soon_batches: number
  batches: number
  lots: number
}

export interface InventoryBatchList {
  items: InventoryBatch[]
  total: number
  page: number
  page_size: number
  as_of: string
  /** WMS lots behind the batches — so the screen can say "128 batches
   *  (543 lots)" rather than leaving a reader to wonder where they went. */
  total_lots: number
  /** Over EVERY matching row, not just this page, and in the SAME response as
   *  the rows — two endpoints taking the same filters drift apart. */
  summary: InventorySummaryLine[]
  /** The horizon the server warned on, so the label cannot fall out of step
   *  with the query behind it. */
  expiry_warning_days: number
}

export interface InventoryLot {
  id: string
  warehouse_id: string
  material_code: string
  material_name: string | null
  lot_no: string
  qty: string
  qty_allocated: string
  qty_onhold: string
  wms_status: string | null
  mapped_status: string
  production_date: string | null
  expiry_date: string | null
  inbound_date: string | null
  /** Negative once past. Null when the lot has no expiry date at all. */
  days_to_expiry: number | null
  /** Null for a lot that does not expire — not a bucket, and never rendered
   *  as one. */
  aging_bucket: AgingBucketKey | null
  supplier_batch: string | null
  supplier_code: string | null
  source_doc: string | null
  wms_edit_time: string | null
  sync_batch_id: string
}

export interface InventoryLotList {
  items: InventoryLot[]
  total: number
  page: number
  page_size: number
  /** The date the server bucketed and counted against. Rendered on screen so
   *  a stale tab cannot silently disagree with it. */
  as_of: string
}

export interface AgingBucket {
  key: AgingBucketKey
  label: string
  /** WMS lots — the physical count. Secondary: the list below the cards is
   *  batches, so `batches` is the number that has to match its row count. */
  lots: number
  batches: number
  qty: string
}

export interface AgingSummary {
  as_of: string
  /** Always all five, even at zero — a missing band would read as "not
   *  computed" rather than "nothing here". */
  buckets: AgingBucket[]
  no_expiry_lots: number
  no_expiry_batches: number
  no_expiry_qty: string
  erp_class_code: string | null
}

export interface MaterialStock {
  material_code: string
  material_name: string | null
  base_uom: string | null
  erp_class_code: string | null
  erp_class_name: string | null
  on_hand: string
  available: string
  allocated: string
  on_hold: string
  expired_qty: string
  next_expiry: string | null
  days_to_next_expiry: number | null
  lots: number
  in_transit: string
  earliest_arrival: string | null
  open_po_lines: number
}

export interface MaterialStockList {
  items: MaterialStock[]
  total: number
  page: number
  page_size: number
  as_of: string
}

export interface OpenPoLine {
  po_number: string
  vendor_name: string
  material_code: string
  ordered: string
  received: string
  remaining: string
  unit: string
  expected_arrival: string | null
  /** True when the date is the PO header's hand-entered one rather than the
   *  ERP's own line-level date. Shown, because a planner chasing a late
   *  delivery deserves to know which they are looking at. */
  arrival_is_from_header: boolean
}

export interface BatchFilters {
  search?: string
  material_code?: string
  mapped_status?: string
  warehouse_id?: string
  erp_class_code?: string
  expiring_before?: string
  expiring_after?: string
  aging_bucket?: AgingBucketKey
  /** CF00AF Animal Feed and CF00WT Waste Powder — filed as finished goods but
   *  not sellable product, and excluded unless asked for. CF00AF alone is 45%
   *  of finished-goods stock by quantity, so leaving them in means the totals
   *  describe the feed pile. */
  include_byproducts?: boolean
  sort?: 'material_code' | 'supplier_batch' | 'production_date' | 'expiry_date'
    | 'qty' | 'inbound_date'
  descending?: boolean
}

export interface LotFilters {
  search?: string
  material_code?: string
  mapped_status?: string
  warehouse_id?: string
  erp_class_code?: string
  expiring_before?: string
  expiring_after?: string
  /** Restrict to one shelf-life band. Resolved SERVER-side from the same
   *  thresholds the summary counts with, so a band's card and the rows behind
   *  it cannot disagree — rebuilding the date windows here was off by one at
   *  three of the five boundaries. */
  aging_bucket?: AgingBucketKey
  sort?: 'material_code' | 'lot_no' | 'expiry_date' | 'qty' | 'inbound_date'
  descending?: boolean
}

function params(page: number, pageSize: number, filters: Record<string, unknown>): string {
  const q = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === '') continue
    if (typeof value === 'boolean') {
      if (value) q.set(key, 'true')
      continue
    }
    q.set(key, String(value))
  }
  return q.toString()
}

export const inventoryApi = {
  /** Stock grouped by supplier batch — what every list on this screen shows.
   *  `listLots` below is the raw per-WMS-lot mirror and no screen uses it. */
  listBatches: (page: number, pageSize: number, filters: BatchFilters = {}) =>
    api.get<InventoryBatchList>(`/inventory/batches?${params(page, pageSize, { ...filters })}`),

  /** Where one batch sits. `supplierBatch` null means the batch of stock that
   *  carries no supplier batch — a real row on the list, not an absence. */
  batchLocations: (materialCode: string, supplierBatch: string | null,
                   warehouseId?: string) => {
    const q = new URLSearchParams({ material_code: materialCode })
    if (supplierBatch !== null) q.set('supplier_batch', supplierBatch)
    if (warehouseId) q.set('warehouse_id', warehouseId)
    return api.get<BatchLocation[]>(`/inventory/batches/locations?${q}`)
  },

  listLots: (page: number, pageSize: number, filters: LotFilters = {}) =>
    api.get<InventoryLotList>(`/inventory/lots?${params(page, pageSize, { ...filters })}`),

  aging: (opts: { erp_class_code?: string; warehouse_id?: string
                  include_byproducts?: boolean } = {}) => {
    const q = new URLSearchParams()
    if (opts.erp_class_code) q.set('erp_class_code', opts.erp_class_code)
    if (opts.warehouse_id) q.set('warehouse_id', opts.warehouse_id)
    if (opts.include_byproducts) q.set('include_byproducts', 'true')
    const suffix = q.toString()
    return api.get<AgingSummary>(`/inventory/aging${suffix ? `?${suffix}` : ''}`)
  },

  listMaterials: (
    page: number, pageSize: number,
    filters: { search?: string; erp_class_code?: string; only_with_stock?: boolean
               include_byproducts?: boolean } = {},
  ) => api.get<MaterialStockList>(`/inventory/materials?${params(page, pageSize, { ...filters })}`),

  openPoLines: (materialCode: string) =>
    api.get<OpenPoLine[]>(
      `/inventory/materials/${encodeURIComponent(materialCode)}/open-po-lines`),
}

/** ERP/NC material classes, for the class filter.
 *
 *  `0101 Raw Milk` is deliberately absent: it is excluded server-side from
 *  every stock and on-order figure (business rule, 2026-08-17), so offering it
 *  as a filter would produce a permanently empty screen with no explanation.
 *  Counts are from the dev snapshot and are indicative only — the filter reads
 *  whatever the master says today. */
export const MATERIAL_CLASSES: { code: string; label: string }[] = [
  { code: '0102', label: 'Raw Ingredient' },
  { code: '02', label: 'Packaging Material' },
  { code: '03', label: 'Standardized Milk' },
  { code: '04', label: 'Storage Silo Powder' },
  { code: '05', label: 'Finished Products' },
  { code: '06', label: 'Chemical' },
  { code: '07', label: 'Mechanical' },
  { code: '08', label: 'Laboratory' },
]

// Pure display formatters live in ./format so they can be exercised without
// dragging the API client in. Re-exported so call sites keep one import.
export { qty, formatDateOnly } from './format'
