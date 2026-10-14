// inventory-api: Reconciliation (inventory-api/app/api/v1/recon.py, logic in
// app/services/recon.py; design §7.3, §8). Gated inventory.view; every amount
// is null unless the caller holds inventory.cost.view — the page hides amount
// columns from the response's `amounts_visible`, never from its own check.
//
// Numbers are Decimal strings (format with lib/number.ts); dates are plain
// "YYYY-MM-DD" strings, never passed through new Date().
import { api } from '@/lib/api'
import { qs } from '@/pages/nc/ncApi'

export type ReconStatus = 'ok' | 'explained' | 'unexplained'
export type ReconSide = 'ic_wms' | 'ia_ic' | 'gl_ia'
export type ReconSort = 'material' | 'ic_wms' | 'ia_ic' | 'gl_ia'

export const STATUSES: { key: ReconStatus; label: string }[] = [
  { key: 'unexplained', label: 'Unexplained' },
  { key: 'explained', label: 'Explained' },
  { key: 'ok', label: 'OK' },
]

export const SIDE_LABELS: Record<ReconSide, string> = {
  ic_wms: 'IC−WMS',
  ia_ic: 'IA−IC',
  gl_ia: 'GL−IA',
}

import type { WmsHistory } from '@/lib/wmsHistory'

export interface ReconCategoryDef {
  code: string
  side: ReconSide
  label: string
}

export interface ReconCause {
  code: string
  label: string
  side: ReconSide
  qty: string
  /** null without inventory.cost.view, or when the cause is quantity-only. */
  amount: string | null
}

export interface ReconBatchGroup {
  label: string
  nc_batches: string[]
  wms_lots: string[]
  supplier_batches: string[]
  match: 'both' | 'wms_only' | 'nc_only'
  /** null before WMS went live. */
  wms: string | null
  ic_wms: string
  ic_other: string
  ia: string
  diff_ic_wms: string | null
}

export interface ReconRow {
  material_code: string
  material_name: string | null
  uom: string | null
  class_code: string | null
  in_nc: boolean
  wms_uom: string | null
  unit_check: 'match' | 'mismatch' | 'unknown'
  no_batch_since: string | null
  /** null before WMS went live. */
  wms: string | null
  ic_wms: string
  ic_external: string
  ic_production: string
  /** Transient + other + unclassified warehouses (in ic_total only). */
  ic_transient: string
  ic_total: string
  ia_qty: string
  gl_qty: string
  ia_amount: string | null
  gl_amount: string | null
  diff: { ic_wms: string | null; ia_ic: string; gl_ia_qty: string; gl_ia_amount: string | null }
  /** What is left of each diff after the causes; "unexplained" when above tolerance. */
  unexplained: { ic_wms: string | null; ia_ic: string; gl_ia_qty: string; gl_ia_amount: string | null }
  categories: ReconCause[]
  /** GL non-costing entries by source system; null without inventory.cost.view. */
  gl_by_source: Record<string, string> | null
  status: ReconStatus
  batch_expandable: boolean
  /** Present only with expand_batches=true and batch_expandable. */
  batches?: ReconBatchGroup[]
}

export interface ReconGlExtra {
  account_code: string
  account_name: string | null
  qty: string | null
  amount: string | null
}

export interface ReconResponse {
  date: string
  nc_time_zone: string
  period: string
  wms_live: boolean
  wms_history: WmsHistory
  include_unposted: boolean
  amounts_visible: boolean
  tolerance: { qty: string; amount: string }
  categories: ReconCategoryDef[]
  totals: {
    wms: string | null
    ic_wms: string
    ic_external: string
    ic_production: string
    ic_transient: string
    ic_total: string
    ia_qty: string
    gl_qty: string
    ia_amount: string | null
    gl_amount: string | null
    materials: number
    by_status: Record<ReconStatus, number>
  }
  total: number
  page: number
  page_size: number
  items: ReconRow[]
  gl_impairment: ReconGlExtra[]
  gl_no_material: ReconGlExtra[]
}

export interface ReconFilters {
  date: string
  expand_batches: boolean
  status: ReconStatus[]
  category: string[]
  class_code?: string
  search?: string
  include_unposted: boolean
  sort: ReconSort
  page: number
  page_size: number
}

export const reconApi = {
  get: (f: ReconFilters) => api.get<ReconResponse>(`/recon${qs({ ...f })}`),
}
