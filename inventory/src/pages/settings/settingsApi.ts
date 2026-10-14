// inventory-api: Settings page endpoints (inventory-api/app/api/v1/settings.py
// and admin_sync.py). Reads are gated inventory.view, writes inventory.admin.
import { api } from '@/lib/api'

// ── NC sync ──────────────────────────────────────────────────────────────────

export interface NcSyncStatus {
  /** NC65_* env blank = this deployment has no NC connection (Refresh → 503). */
  configured: boolean
  /** Minutes between automatic syncs; 0 = off. */
  interval_minutes: number
  status: 'success' | 'failed' | null
  /** Last ATTEMPT (moves even when it failed). */
  last_synced_at: string | null
  /** Last attempt that replaced the mirrors — the age of what is on screen. */
  last_success_at: string | null
  last_error: string | null
  row_counts: Record<string, number>
  filtered_counts: Record<string, number>
  duration_ms: number | null
  tieout_ok: boolean | null
  tieout_issue_count: number
  next_due_at: string | null
  running: boolean
}

export interface TieoutIssue {
  check: string
  measure: 'qty' | 'amount' | string
  key: string
  label: string | null
  mirror_value: string | null
  nc_value: string | null
}

export interface TieoutIssues {
  /** Rows stored (capped per check); tieout_issue_count on the status is the
   *  uncapped total. */
  total_stored: number
  by_check: Record<string, number>
  items: TieoutIssue[]
}

export interface NcSyncResult {
  status: 'success'
  rows: Record<string, number>
  filtered: Record<string, number>
  tieout_ok: boolean
  tieout_issues: Record<string, number>
  duration_ms: number
}

// ── Parameters ───────────────────────────────────────────────────────────────

export const PARAM_SYNC_INTERVAL = 'nc_sync_interval_minutes'
export const PARAM_NC_TIME_ZONE = 'nc_time_zone'
export const PARAM_QTY_TOLERANCE = 'qty_tolerance'
export const PARAM_AMOUNT_TOLERANCE = 'amount_tolerance'

export interface ParamValues {
  [PARAM_SYNC_INTERVAL]: number
  [PARAM_NC_TIME_ZONE]: string
  [PARAM_QTY_TOLERANCE]: number
  [PARAM_AMOUNT_TOLERANCE]: number
}

export interface ParamsResponse {
  values: ParamValues
  defaults: ParamValues
  nc_time_zones: string[]
}

// ── Warehouse classes ────────────────────────────────────────────────────────

export type WarehouseClass = 'wms' | 'external' | 'production' | 'transient' | 'other'

export interface WarehouseClassRow {
  code: string
  name: string | null
  /** false = NC does not cost this warehouse (SC16); null = not in NC. */
  costed: boolean | null
  warehouse_class: WarehouseClass | 'unclassified'
  note: string | null
  on_hand_qty: string
  /** false = classified here but NC no longer has the warehouse. */
  in_nc: boolean
  updated_at: string | null
}

export interface WarehouseClassesResponse {
  classes: WarehouseClass[]
  items: WarehouseClassRow[]
  unclassified_count: number
}

// ── Materials not tracked by batch in NC ─────────────────────────────────────

export interface NoBatchMaterial {
  material_code: string
  /** Plain date "YYYY-MM-DD" — never pass through new Date() (UTC-4 shifts it
   *  a day back). */
  since_date: string
  note: string | null
}

export const settingsApi = {
  syncStatus: () => api.get<NcSyncStatus>('/admin/nc-sync/status'),
  runSync: () => api.post<NcSyncResult>('/admin/nc-sync'),
  tieoutIssues: () => api.get<TieoutIssues>('/admin/nc-sync/tieout-issues?limit=1000'),

  params: () => api.get<ParamsResponse>('/settings/params'),
  putParam: (key: string, value: unknown) =>
    api.put<{ key: string; value: unknown }>(`/settings/params/${encodeURIComponent(key)}`, { value }),

  warehouseClasses: () => api.get<WarehouseClassesResponse>('/settings/warehouse-classes'),
  putWarehouseClass: (code: string, warehouse_class: WarehouseClass, note: string | null) =>
    api.put<{ code: string; warehouse_class: string }>(
      `/settings/warehouse-classes/${encodeURIComponent(code)}`, { warehouse_class, note }),

  noBatchMaterials: () => api.get<NoBatchMaterial[]>('/settings/no-batch-materials'),
  putNoBatchMaterial: (code: string, since_date: string, note: string | null) =>
    api.put<{ material_code: string; since_date: string }>(
      `/settings/no-batch-materials/${encodeURIComponent(code)}`, { since_date, note }),
  deleteNoBatchMaterial: (code: string) =>
    api.delete<void>(`/settings/no-batch-materials/${encodeURIComponent(code)}`),
}
