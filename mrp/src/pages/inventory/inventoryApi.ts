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

export type AgingBucketKey = 'expired' | 'under_30' | '30_to_60' | '60_to_180' | 'over_180'

/** Order matters: most urgent first, and the screen renders them in this
 *  order without re-sorting. Mirrors AGING_BUCKETS in
 *  mrp-api/app/services/inventory_aging.py. */
export const AGING_BUCKET_ORDER: AgingBucketKey[] = [
  'expired', 'under_30', '30_to_60', '60_to_180', 'over_180',
]

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
  lots: number
  qty: string
}

export interface AgingSummary {
  as_of: string
  /** Always all five, even at zero — a missing band would read as "not
   *  computed" rather than "nothing here". */
  buckets: AgingBucket[]
  no_expiry_lots: number
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
  listLots: (page: number, pageSize: number, filters: LotFilters = {}) =>
    api.get<InventoryLotList>(`/inventory/lots?${params(page, pageSize, { ...filters })}`),

  aging: (opts: { erp_class_code?: string; warehouse_id?: string } = {}) => {
    const q = new URLSearchParams()
    if (opts.erp_class_code) q.set('erp_class_code', opts.erp_class_code)
    if (opts.warehouse_id) q.set('warehouse_id', opts.warehouse_id)
    const suffix = q.toString()
    return api.get<AgingSummary>(`/inventory/aging${suffix ? `?${suffix}` : ''}`)
  },

  listMaterials: (
    page: number, pageSize: number,
    filters: { search?: string; erp_class_code?: string; only_with_stock?: boolean } = {},
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

/** Quantities are Decimal-as-string on the wire. One place converts them, so
 *  a column cannot quietly start rendering "1000.0000". */
export function qty(value: string | null | undefined, fractionDigits = 0): string {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return value
  return n.toLocaleString('en-US', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })
}

/** ★ Date-ONLY values (expiry, arrival, inbound) must never go through
 *  `new Date(...)`: in this timezone that parses as UTC midnight and renders
 *  as the previous day — across a year boundary, the previous YEAR. These
 *  arrive as 'YYYY-MM-DD' and are formatted by slicing, never by parsing. */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function formatDateOnly(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.slice(0, 10).split('-')
  const month = MONTHS[Number(m) - 1]
  if (!month || !y || !d) return iso
  return `${month} ${Number(d)}, ${y}`
}
