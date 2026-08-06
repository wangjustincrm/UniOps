// Typed client for mdm-api's batch BOM-existence check (Continuous Sales
// Forecast Task 9, spec §8b): a finished good can be forecast BEFORE its BOM
// exists — that's allowed, but the Sales Forecast grid and MPS lines need to
// FLAG it (a "No BOM" badge + row tint). Backend = mdm-api/app/api/v1/
// boms.py's POST /boms/exist. Both pages call the SAME source so "has a BOM"
// is defined in exactly one place, not re-derived per page. Uses `mdmApi`
// (see lib/api.ts), same client bomApi.ts / lib/materials.ts already rely on.
import { mdmApi } from '@/lib/api'

interface BomExistResponse {
  with_bom: string[]
}

export const bomStatusApi = {
  /** Posts the given product codes and returns the subset that has at least
   *  one APPROVED bom row, as a Set for O(1) per-row lookup. Empty input
   *  short-circuits to an empty Set without a network round trip. */
  withBom: async (codes: string[]): Promise<Set<string>> => {
    if (codes.length === 0) return new Set()
    const res = await mdmApi.post<BomExistResponse>('/mdm/v1/boms/exist', { product_codes: codes })
    return new Set(res.with_bom)
  },
}
