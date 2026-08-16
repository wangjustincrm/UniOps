// Typed client for mrp-api's /purchase endpoints (Phase 1C).
//
// A run is one calculation over the production plan in force, stored rather
// than recomputed on read: a buyer working through the list must not have
// it shift under them because stock moved.
//
// Decimals arrive as strings — wrap in Number() before arithmetic.
import { api } from '@/lib/api'

export type PurchaseLineStatus = 'pending' | 'ordered' | 'ignored'

export interface PurchaseLine {
  id: string
  material_code: string
  /** The week the material is needed, and the date to order by (need week
   *  minus the supplier's lead time). */
  need_week: string
  order_date: string
  gross_qty: string
  available_qty: string
  net_qty: string
  suggested_qty: string
  /** How much of `suggested_qty` exists only because the requirement was
   *  below the supplier's minimum order quantity. */
  raised_to_moq: string
  partner_code: string | null
  lead_time_days: number | null
  /** Each unknown carries its own flag because each needs a different fix:
   *  add a supplier, fill in a lead time, or expedite an order that is
   *  already late. */
  supplier_missing: boolean
  lead_time_missing: boolean
  order_date_passed: boolean
  status: PurchaseLineStatus
}

export interface PurchaseRunStats {
  products_in_plan: number
  products_without_bom: number
  components: number
  lines: number
  supplier_missing: number
  lead_time_missing: number
  order_date_passed: number
  raw_material_loss_rate: string
  packaging_loss_rate: string
}

export interface PurchaseRun {
  id: string
  run_no: string
  source_plan_run_id: string | null
  raw_material_loss_rate: string
  packaging_loss_rate: string
  stats: PurchaseRunStats | null
  created_at: string
  lines: PurchaseLine[]
}

export interface PurchaseRunSummary {
  id: string
  run_no: string
  source_plan_run_id: string | null
  stats: PurchaseRunStats | null
  created_at: string
}

export const purchaseApi = {
  list: () => api.get<PurchaseRunSummary[]>('/purchase/runs'),
  get: (runId: string) => api.get<PurchaseRun>(`/purchase/runs/${runId}`),
  /** 409s when no production plan is in force — with nothing released
   *  there is no demand to buy for, and an empty run would read as
   *  "nothing to order". */
  create: () => api.post<PurchaseRun>('/purchase/runs', {}),
  setStatus: (runId: string, lineId: string, status: PurchaseLineStatus) =>
    api.patch<PurchaseLine>(`/purchase/runs/${runId}/lines/${lineId}`, { status }),
}
