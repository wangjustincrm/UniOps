// Typed client for mdm-api's BOM endpoints (design spec §6.6 page 6; backend
// = mdm-api/app/api/v1/boms.py, task 5/6/7). Mounted under mdm-api's own
// /mdm/v1 prefix, not mrp-api's /api/v1 — uses `mdmApi` (see lib/api.ts),
// same client the Consignment Stock product picker already relies on
// (lib/materials.ts).
//
// Decimal fields (qty_per, qty_accumulated, scrap_rate, qty_per_secondary)
// arrive as JSON strings, same as every other Decimal field in this repo —
// see feedback_uniops_decimal_as_string. Callers must Number() them before
// arithmetic or formatting; this file intentionally leaves them as strings
// on the wire types so a forgotten Number() fails loudly (NaN / "0.4120"
// concatenation) instead of silently working in dev and lying in prod.
import { mdmApi } from '@/lib/api'

export type BomType = 'milling' | 'drymix' | 'packaging' | 'raw' | 'packaging-material' | null

export interface ExplodeNode {
  material_code: string
  name: string | null
  bom_type: string | null
  level: number
  qty_per: string
  qty_accumulated: string
  uom: string | null
  qty_per_secondary: string | null
  uom_secondary: string | null
  scrap_rate: string
  version: string | null
  version_candidates_count: number
  missing_bom: boolean
  cycle_detected: boolean
  node_limit_reached: boolean
  children: ExplodeNode[]
}

export interface WhereUsedResult {
  top_product: string
  path: string[]
  qty_accumulated: string
  levels: number
  cycle_detected: boolean
}

export interface BomSyncStats {
  boms: number
  lines: number
  substitutes: number
  skipped: number
  warnings: number
  tombstoned: number
  tombstone_skipped: string[]
}

export interface BomSyncStateResponse {
  source: string
  last_success_at: string | null
  last_error: string | null
  last_stats: BomSyncStats | null
  updated_at: string | null
}

// The 503 detail text mdm-api's POST /sync raises verbatim when
// nc_configured() is false (app/api/v1/boms.py) — there is no dedicated
// "is NC configured" read endpoint, so the button's pre-click disabled
// state is inferred from whether the LAST recorded sync attempt failed
// with exactly this message (best-effort from history, not a live check;
// see BomExplorerPage's Sync section comment for the full reasoning).
export const NC_NOT_CONFIGURED_DETAIL = 'NC connection is not configured'

export const bomApi = {
  explode: (product: string, date: string, maxDepth = 10) =>
    mdmApi.get<ExplodeNode>(
      `/mdm/v1/boms/explode?product=${encodeURIComponent(product)}&date=${date}&max_depth=${maxDepth}`,
    ),

  whereUsed: (component: string, date: string) =>
    mdmApi.get<WhereUsedResult[]>(
      `/mdm/v1/boms/where-used?component=${encodeURIComponent(component)}&date=${date}`,
    ),

  syncState: () => mdmApi.get<BomSyncStateResponse>('/mdm/v1/boms/sync-state'),

  sync: () => mdmApi.post<BomSyncStats>('/mdm/v1/boms/sync'),
}
