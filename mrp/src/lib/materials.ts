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
  base_uom: string | null
  is_active: boolean
}

export interface MaterialListResponse {
  items: MaterialOption[]
  total: number
  page: number
  page_size: number
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
