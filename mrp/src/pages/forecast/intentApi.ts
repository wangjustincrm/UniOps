// Typed client for mrp-api's /intent-products endpoints (Continuous Sales
// Forecast follow-up, "intent products" — design §5.3 decisions D7/D8/D11).
// An intent product is a planned SKU recorded under an `INTENT-xxxxxxxx`
// placeholder `material_code` before a real ERP material code exists for it
// — see mrp-api/app/models/intent.py's docstring: it rides the ordinary
// mrp_demand_series table, so the whole grid stack (paste, autosave, change
// log, KG/t toggle) needs no special case there. This client only covers
// the lifecycle table itself (mrp-api/app/api/v1/intent.py).
//
// Same `api` import path/method names as forecastApi.ts/seriesApi.ts (see
// lib/api.ts's header — absolute URLs from VITE_API_URL, no working Vite
// dev proxy in this repo, feedback_uniops_oa_vite_proxy_dead in project
// memory). `api`'s request() already prefixes every path with mrp-api's
// mounted `/api/v1`, so paths below start at `/intent-products`, not
// `/api/v1/intent-products`.
import { api } from '@/lib/api'

/** mrp-api's placeholder code prefix (app/services/intent_products.py's
 *  INTENT_CODE_PREFIX) — the one client-side signal an arbitrary grid row id
 *  is an intent product rather than a real material, independent of whether
 *  it's still 'active' in the list below (a code keeps this prefix through
 *  its whole lifecycle up to the moment `bind` moves its data off it). */
const INTENT_CODE_PREFIX = 'INTENT-'

export function isIntentCode(code: string): boolean {
  return code.startsWith(INTENT_CODE_PREFIX)
}

export type IntentProductStatus = 'active' | 'bound' | 'dropped'

export interface IntentProduct {
  id: string
  code: string
  name: string
  note: string | null
  status: IntentProductStatus
  bound_material_code: string | null
}

export interface IntentBindResult {
  intent: IntentProduct
  moved_months: number
  // Decimal-as-string on the wire — see feedback_uniops_decimal_as_string in
  // project memory; convert with Number() at the display site, never trust
  // this as a number here.
  moved_qty: string
}

export const intentApi = {
  /** `status` mirrors the backend's own default ('active') — pass 'all' to
   *  also see bound/dropped rows. Sales Forecast only ever needs the
   *  default: a bound/dropped intent product is no longer a placeholder row
   *  to flag. */
  list: (status: 'active' | 'all' = 'active') =>
    api.get<IntentProduct[]>(`/intent-products?status=${status}`),

  /** Blank/whitespace-only name 422s server-side too (IntentProductCreate's
   *  validator) — callers should still pre-validate on blur so the error
   *  shows before a round trip. */
  create: (name: string, note?: string) =>
    api.post<IntentProduct>('/intent-products', { name, note }),

  /** 409s when `materialCode` already carries forecast rows, or when the
   *  intent product is no longer 'active' (already bound/dropped) — see
   *  intent.py's bind_intent_product. The 409 detail is a deliberately
   *  human-readable sentence (decision D11); callers must surface it
   *  verbatim, not swallow it into a generic message. */
  bind: (id: string, materialCode: string) =>
    api.post<IntentBindResult>(`/intent-products/${id}/bind`, { material_code: materialCode }),
}
