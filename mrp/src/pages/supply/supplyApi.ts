// Typed client for mdm-api's /material-suppliers endpoints — the supply
// parameters Phase 1C's purchase suggestions are computed from.
//
// These live in mdm-api (master data), not mrp-api: a material's lead time
// and minimum order quantity are facts about the material, not about one
// planning run. mrp reaches them the same way it reaches materials and
// BOMs, through `mdmApi` (see lib/api.ts).
//
// The table is HAND-MAINTAINED: nothing syncs it from ERP or NC, and before
// this page there was no interface for it at all, so it was reachable only
// by calling the API directly.
import { mdmApi } from '@/lib/api'

export interface MaterialSupplier {
  id: string
  material_code: string
  partner_code: string
  /** Days between placing an order and receiving it — what the purchase
   *  suggestion works backwards from to get an order-by date. */
  lead_time_days: number | null
  /** Minimum order quantity. A requirement below it is raised to it. */
  moq: string | null
  order_multiple: string | null
  is_primary: boolean
  price_ref: string | null
  notes: string | null
}

export interface MaterialSupplierList {
  items: MaterialSupplier[]
  total: number
  page: number
  page_size: number
}

export interface BulkRow {
  material_code: string
  partner_code: string
  lead_time_days?: number | null
  moq?: string | null
  order_multiple?: string | null
  is_primary?: boolean
  price_ref?: string | null
  notes?: string | null
}

export interface BulkResult {
  created: number
  updated: number
  errors: { row: number; material_code: string; partner_code: string; message: string }[]
}

export const supplyApi = {
  /** Every row, paged through. The screen filters client-side, and filtering
   *  only the first page would quietly hide rows that match — a search box
   *  that misses is worse than none. */
  listAll: async (): Promise<MaterialSupplier[]> => {
    const pageSize = 500
    const all: MaterialSupplier[] = []
    let page = 1
    for (;;) {
      const res = await mdmApi.get<MaterialSupplierList>(
        `/mdm/v1/material-suppliers?page=${page}&page_size=${pageSize}`)
      all.push(...res.items)
      if (res.items.length === 0 || all.length >= res.total) break
      page++
    }
    return all
  },

  /** Supplier code -> name, so the list can show who "001" is and the search
   *  box can match on the name people actually know. */
  supplierNames: async (): Promise<Map<string, string>> => {
    const pageSize = 500
    const names = new Map<string, string>()
    let page = 1
    for (;;) {
      const res = await mdmApi.get<{ items: { code: string; name: string }[]; total: number }>(
        `/mdm/v1/partners?role=supplier&page=${page}&page_size=${pageSize}`)
      for (const p of res.items) names.set(p.code, p.name)
      if (res.items.length === 0 || names.size >= res.total) break
      page++
    }
    return names
  },

  list: (page = 1, pageSize = 200) =>
    mdmApi.get<MaterialSupplierList>(
      `/mdm/v1/material-suppliers?page=${page}&page_size=${pageSize}`),

  create: (body: BulkRow) =>
    mdmApi.post<MaterialSupplier>('/mdm/v1/material-suppliers', body),

  update: (id: string, body: Partial<BulkRow>) =>
    mdmApi.patch<MaterialSupplier>(`/mdm/v1/material-suppliers/${id}`, body),

  remove: (id: string) => mdmApi.del<void>(`/mdm/v1/material-suppliers/${id}`),

  /** Rows are upserted by (material_code, partner_code) and reported
   *  individually — one bad row never discards the rest of a paste. */
  bulk: (rows: BulkRow[]) =>
    mdmApi.post<BulkResult>('/mdm/v1/material-suppliers/bulk', { rows }),
}

/** Parse a tab-separated paste (Excel) into bulk rows.
 *
 *  Column order is fixed and stated on screen:
 *  `material | supplier | lead time (days) | MOQ | order multiple | primary`.
 *  A header row is skipped when its first cell is not a plausible material
 *  code — pasting straight from a spreadsheet usually brings one along, and
 *  making the planner delete it by hand is the kind of friction that sends
 *  the data back to Excel for good.
 */
export function parsePaste(text: string): { rows: BulkRow[]; skippedHeader: boolean } {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.length > 0)
  if (lines.length === 0) return { rows: [], skippedHeader: false }

  const cells = lines.map((line) => line.split('\t').map((c) => c.trim()))
  const first = cells[0]
  const skippedHeader = /^(material|item|物料)/i.test(first[0] ?? '')
  const body = skippedHeader ? cells.slice(1) : cells

  const rows: BulkRow[] = body.map((c) => ({
    material_code: c[0] ?? '',
    partner_code: c[1] ?? '',
    lead_time_days: c[2] ? Number(c[2]) : null,
    moq: c[3] || null,
    order_multiple: c[4] || null,
    is_primary: /^(y|yes|true|1|是)$/i.test(c[5] ?? ''),
  }))
  return { rows, skippedHeader }
}
