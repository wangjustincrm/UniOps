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
}
