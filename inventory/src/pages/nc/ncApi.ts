// inventory-api: NC Inventory page endpoints (inventory-api/app/api/v1/nc_inventory.py).
// All gated inventory.view. Amount fields are null unless the caller holds
// inventory.cost.view — the response's `amounts_visible` says which, and the
// page hides amount columns from that flag, never from its own permission
// check.
//
// Every number is a Decimal serialised as a string; format with lib/number.ts.
// Every date is a plain string ("YYYY-MM-DD", or NC's raw "YYYY-MM-DD HH:MM:SS"
// on its own clock) — shown as-is, never passed through new Date().
import type { SortDir } from '@uniops/shell'
import { api } from '@/lib/api'

export const MATERIAL_CLASSES = [
  { code: '0101', label: 'Raw Milk' },
  { code: '0102', label: 'Raw Ingredient' },
  { code: '02', label: 'Packaging' },
  { code: '04', label: 'Powder (CS)' },
  { code: '05', label: 'Finished (CF)' },
] as const

export const WAREHOUSE_CLASSES = [
  { key: 'wms', label: 'WMS' },
  { key: 'external', label: 'External' },
  { key: 'production', label: 'Production' },
  { key: 'transient', label: 'Transient' },
  { key: 'other', label: 'Other' },
  { key: 'unclassified', label: 'Unclassified' },
] as const

export type WarehouseClassKey = typeof WAREHOUSE_CLASSES[number]['key']

// ── Meta ─────────────────────────────────────────────────────────────────────

export type IaPeriodStatus = 'closed' | 'open_costed' | 'open_uncosted'

export interface IaStatus {
  last_closed_period: string | null
  status: IaPeriodStatus
  uncosted_rows: number
}

export interface NcMeta {
  date: string
  today: string
  is_today: boolean
  nc_time_zone: string
  /** NC-clock end of the chosen Toronto day, "YYYY-MM-DD HH:MM:SS". */
  nc_cutoff: string
  period: string
  month_end: boolean
  ia: IaStatus
  undated_ic_rows_with_qty: number
  amounts_visible: boolean
  sync: {
    last_success_at: string | null
    status: string | null
    tieout_ok: boolean | null
    tieout_issue_count: number
  }
}

// ── Stock (IC) ───────────────────────────────────────────────────────────────

/** Sort keys the API whitelists (inventory-api nc_inventory.py), one per column. */
export type StockBatchSort = 'warehouse' | 'material' | 'batch' | 'supplier_batch' | 'produced'
  | 'expires' | 'qty' | 'locked' | 'available' | 'state'
export type StockMaterialSort = 'material' | 'qty' | 'qty_wms' | 'qty_external' | 'qty_production'
  | 'qty_other' | 'batches' | 'locked'

export interface StockSummaryRow {
  warehouse_class: string
  materials: number
  lines: number
  qty: string | null
}

export interface StockBatchRow {
  warehouse_code: string | null
  warehouse_name: string | null
  warehouse_class: string
  costed: boolean | null
  material_code: string | null
  material_name: string | null
  uom: string | null
  class_code: string | null
  batch: string | null
  supplier_batch: string | null
  produced: string | null
  expires: string | null
  qty: string | null
  /** null on historical dates (state_available false). */
  locked: string | null
  available: string | null
  state: string | null
}

export interface StockMaterialRow {
  material_code: string | null
  material_name: string | null
  uom: string | null
  class_code: string | null
  qty: string | null
  qty_wms: string | null
  qty_external: string | null
  qty_production: string | null
  qty_other: string | null
  locked: string | null
  batches: number
}

interface StockBase {
  date: string
  is_today: boolean
  state_available: boolean
  summary: StockSummaryRow[]
  lock_residue_rows: number
  total: number
  page: number
  page_size: number
}

export type StockResponse =
  | (StockBase & { group: 'batch'; items: StockBatchRow[] })
  | (StockBase & { group: 'material'; items: StockMaterialRow[] })

// ── Cost (IA) ────────────────────────────────────────────────────────────────

export type CostSort = 'material' | 'qty' | 'amount' | 'unit_price' | 'uncosted'

export interface CostRow {
  material_code: string | null
  material_name: string | null
  uom: string | null
  class_code: string | null
  qty: string | null
  amount: string | null
  unit_price: string | null
  uncosted_rows: number
}

export interface CostResponse {
  date: string
  period: string
  ia: IaStatus
  amounts_visible: boolean
  totals: { materials: number; qty: string | null; amount: string | null }
  total: number
  page: number
  page_size: number
  items: CostRow[]
}

export interface CostBreakdownResponse {
  material_code: string
  date: string
  items: { warehouse_code: string | null; batch: string | null; qty: string | null }[]
}

export interface CostLine {
  period: string | null
  /** NC's raw business date on NC's clock. */
  biz_date: string | null
  direction: 'in' | 'out'
  qty: string | null
  amount: string | null
  uncosted: boolean
  bill_type: string | null
  trantype_code: string | null
  trantype_name: string | null
  bill_code: string | null
  source_code: string | null
  batch: string | null
  warehouse_code: string | null
  estimate: boolean | null
}

export interface CostLinesResponse {
  material_code: string
  date: string
  amounts_visible: boolean
  total: number
  page: number
  page_size: number
  items: CostLine[]
}

// ── Ledger (GL) ──────────────────────────────────────────────────────────────

export interface LedgerAccountRow {
  account_code: string
  account_name: string | null
  qty: string | null
  amount: string | null
}

export interface LedgerRow {
  account_code: string
  account_name: string | null
  /** null = the lines carry no material auxiliary (material_name is then
   *  "(no material)"), or the material is missing from the NC mirror
   *  (material_name null). */
  material_code: string | null
  /** The material auxiliary's pk; null only when the lines carry none.
   *  Drill-down uses it when the code is unknown. */
  material_pk: string | null
  material_name: string | null
  uom: string | null
  qty: string | null
  amount: string | null
  unposted_lines: number
}

export interface LedgerResponse {
  date: string
  period: string
  include_unposted: boolean
  amounts_visible: boolean
  accounts: LedgerAccountRow[]
  /** 1471 impairment provision — shown on its own, never per material. */
  impairment: LedgerAccountRow[]
  total: number
  page: number
  page_size: number
  items: LedgerRow[]
}

export interface LedgerLine {
  year: string
  period: string
  voucher_num: number | null
  /** NC's raw prepared date on NC's clock. */
  prepared: string | null
  posted: boolean
  source: string | null
  explanation: string | null
  qty: string | null
  amount: string | null
}

export interface LedgerLinesResponse {
  account_code: string
  material_code: string | null
  date: string
  amounts_visible: boolean
  total: number
  page: number
  page_size: number
  items: LedgerLine[]
}

// ── Calls ────────────────────────────────────────────────────────────────────

export interface CommonFilters {
  date: string
  class_code?: string
  search?: string
  page: number
  page_size: number
  /** Column sort; absent = the API's default order. */
  sort?: string
  dir?: SortDir
}

export type LedgerSort = 'account' | 'material' | 'qty' | 'amount'

export function qs(params: Record<string, string | number | boolean | string[] | undefined | null>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === '') continue
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x))
    else sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

const enc = encodeURIComponent

export const ncApi = {
  meta: (date: string) => api.get<NcMeta>(`/nc/meta${qs({ date })}`),

  stock: (f: CommonFilters & { group: 'batch' | 'material'; warehouse_class: string[] }) =>
    api.get<StockResponse>(`/nc/stock${qs({ ...f })}`),

  cost: (f: CommonFilters) => api.get<CostResponse>(`/nc/cost${qs({ ...f })}`),

  costBreakdown: (materialCode: string, date: string) =>
    api.get<CostBreakdownResponse>(`/nc/cost/${enc(materialCode)}/breakdown${qs({ date })}`),

  costLines: (materialCode: string, date: string, page: number, page_size: number) =>
    api.get<CostLinesResponse>(`/nc/cost/${enc(materialCode)}/lines${qs({ date, page, page_size })}`),

  ledger: (f: CommonFilters & { include_unposted: boolean }) =>
    api.get<LedgerResponse>(`/nc/ledger${qs({ ...f })}`),

  ledgerLines: (p: {
    account_code: string; material_code: string | null; material_pk?: string | null; date: string
    include_unposted: boolean; page: number; page_size: number
  }) => api.get<LedgerLinesResponse>(`/nc/ledger/lines${qs({ ...p })}`),
}
