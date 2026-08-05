// Typed client for mrp-api's /consignment/* endpoints (app/api/v1/consignment.py).
// Decimal fields (qty) arrive as JSON strings — same project-wide FastAPI/
// Pydantic gotcha forecastApi.ts documents (feedback_uniops_decimal_as_string
// in project memory) — so qty is typed `string` at the wire boundary and
// converted with Number() at the call site, never trusted as a number here.
import { api } from '@/lib/api'

export type ExpirySource = 'wms' | 'manual' | null

export interface ConsignmentStock {
  id: string
  warehouse_code: string
  material_code: string
  lot_no: string
  qty: string // Decimal-as-string
  count_date: string // YYYY-MM-DD
  expiry_date: string | null
  expiry_source: ExpirySource
  entered_by: string | null
  created_at: string
  updated_at: string
}

export interface ConsignmentStockCreateResponse extends ConsignmentStock {
  // null unless body.expiry_date was omitted (i.e. a WMS auto-lookup was
  // actually attempted) — true/false then reports whether it found the lot.
  // Lets the create-success path distinguish "lot not found in WMS" from
  // "you supplied an explicit manual expiry" without a second round trip.
  wms_lookup_found: boolean | null
}

export interface ConsignmentStockListResponse {
  items: ConsignmentStock[]
  total: number
  page: number
  page_size: number
  // Freshness signal, keyed by warehouse_code (see consignment.py's
  // list_stock()) — the UI only ever shows the 'MAIN' entry today (design
  // spec: single main consignment warehouse), but the shape is per-warehouse
  // so a future second warehouse doesn't need an API change.
  latest_count_dates: Record<string, string>
}

export interface LotLookupResult {
  found: boolean
  production_date: string | null
  expiry_date: string | null
}

export interface CreateStockBody {
  material_code: string
  lot_no: string
  qty: number
  count_date: string
  // Omitted (not just undefined-but-present) so the backend actually
  // attempts a WMS lookup — see consignment.py: "expiry_date omitted ->
  // auto lot-lookup". The page never sends this field itself; a manual
  // expiry override isn't part of this page's scope (design spec only
  // describes auto-fill-or-blank, no manual entry field).
}

export const consignmentApi = {
  listStock: (page = 1, pageSize = 100) =>
    api.get<ConsignmentStockListResponse>(`/consignment/stock?page=${page}&page_size=${pageSize}`),

  createStock: (body: CreateStockBody) =>
    api.post<ConsignmentStockCreateResponse>('/consignment/stock', body),

  lotLookup: (lotNo: string, materialCode: string) =>
    api.get<LotLookupResult>(
      `/consignment/lot-lookup?lot_no=${encodeURIComponent(lotNo)}&material_code=${encodeURIComponent(materialCode)}`,
    ),
}
