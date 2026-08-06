// Typed client for mdm-api's materials master (mdm-api/app/api/v1/materials.py
// GET /mdm/v1/materials). Reads are open to any authenticated role (see that
// file's docstring), so no permission gate is needed here beyond being
// logged in. Used by the searchable product/material dropdown on the
// Consignment Stock page — see pages/consignment/MaterialPicker.tsx.
import { mdmApi } from './api'

export interface MaterialOption {
  id: string
  code: string
  name: string | null
  spec: string | null
  item_type: string | null
  erp_item_type: string | null
  base_uom: string | null
  is_active: boolean
}

export interface MaterialListResponse {
  items: MaterialOption[]
  total: number
  page: number
  page_size: number
}

// Finished-goods classification. The authoritative signal is the ERP's own
// MES material type, mirrored as `erp_item_type`: **type '3' = finished
// good** (per business, 2026-08-06). This beats classifying by code prefix
// (`CF*`/`S<digit>*`): MES type '3' additionally catches finished goods that
// don't follow that naming (e.g. `10821001`/`10821002`, numeric-coded adult
// formulas) and correctly excludes the 13 `CF****-R` *rework* variants
// (type 0/1) that a prefix rule would wrongly sweep in. `item_type`,
// `product_family`, `procurement_type` are unusable (100% NULL or a constant
// default — the webapi sync brings too few attributes); `erp_item_type` is
// populated on all rows. If the ERP-MDM import is later reworked to read the
// ERP DB directly with richer attributes, this is the one spot to revisit.
export const FINISHED_GOODS_MES_TYPE = '3'

// Byproduct codes that carry MES type '3' but are NOT sellable finished goods
// and take no part in BOM / consignment planning (per business, 2026-08-06):
// CF00AF = Animal Feed, CF00WT = Waste Powder. No attribute distinguishes them
// from real finished goods in the thin webapi-synced master, so they're
// excluded by code. Revisit alongside isFinishedGood if the ERP-MDM import is
// reworked to read the ERP DB directly.
export const FINISHED_GOODS_EXCLUDED_CODES = new Set(['CF00AF', 'CF00WT'])

export function isFinishedGood(m: Pick<MaterialOption, 'code' | 'erp_item_type'>): boolean {
  return m.erp_item_type === FINISHED_GOODS_MES_TYPE && !FINISHED_GOODS_EXCLUDED_CODES.has(m.code)
}

export const materialsApi = {
  /** `q` matches code or name (ILIKE, case-insensitive substring) — see materials.py's list_materials(). */
  search: (q: string, pageSize = 30) =>
    mdmApi.get<MaterialListResponse>(
      `/mdm/v1/materials?q=${encodeURIComponent(q)}&page=1&page_size=${pageSize}`,
    ),

  /**
   * Fetches the full materials master, paging at the endpoint's max
   * page_size (500) until exhausted. Used by the Sales Forecast page's
   * row-creating paste (see matrixGrid/pasteLogic.ts's planFullTablePaste)
   * to resolve a whole pasted product-code column in one client-side pass
   * instead of one mdm-api lookup per pasted row — list_materials() has no
   * "match these codes" filter, so a one-time full fetch (cached by the
   * caller's react-query key) is the batched alternative.
   */
  listAll: async (): Promise<MaterialOption[]> => {
    const pageSize = 500
    const all: MaterialOption[] = []
    let page = 1
    for (;;) {
      const res = await mdmApi.get<MaterialListResponse>(
        `/mdm/v1/materials?page=${page}&page_size=${pageSize}`,
      )
      all.push(...res.items)
      if (res.items.length === 0 || all.length >= res.total) break
      page++
    }
    return all
  },
}
