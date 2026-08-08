// Typed client for mrp-api's /capacity/rules CRUD (app/api/v1/capacity.py,
// Phase 1B Task 1 — design §6.4). Plain master-data CRUD: GET is gated by
// `mrp.report.view`, POST/PATCH/DELETE by `mrp.param.write` — the page
// (CapacityRulesPage.tsx) hides/disables the write actions when the caller
// only has the read permission rather than showing a button that 403s.
//
// `limit_value` is Numeric(18,3) server-side; FastAPI/Pydantic serializes
// Decimal as a JSON string, never a float (feedback_uniops_decimal_as_string
// in project memory) — typed `string` on the wire here, `Number()`'d at the
// call site, never trusted as a number in this file.
import { api } from '@/lib/api'

export type CapacityScopeType = 'factory' | 'product_family' | 'line'
export type CapacityConstraintType = 'max_sku_count' | 'max_output_qty'

export interface CapacityRule {
  id: string
  scope_type: CapacityScopeType
  scope_ref: string | null
  constraint_type: CapacityConstraintType
  limit_value: string // Decimal-as-string
  uom: string | null
  effective_from: string // YYYY-MM-DD
  effective_to: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface CapacityRuleBody {
  scope_type: CapacityScopeType
  scope_ref: string | null
  constraint_type: CapacityConstraintType
  limit_value: number
  uom: string | null
  effective_from: string
  effective_to: string | null
  is_active: boolean
}

// PATCH accepts a partial update (backend does `exclude_unset`), but this
// page always resends the whole form on edit, so update() takes the same
// full body shape as create() for a simpler, single form-to-API mapping.
export type CapacityRuleUpdateBody = CapacityRuleBody

// Display labels shared by CapacityRulesPage's table and RuleDrawer's form
// (scope_type/constraint_type selects, plus toast/warning copy) — kept here
// so both stay in sync with one source of truth instead of two ad hoc maps.
export const SCOPE_TYPE_LABEL: Record<CapacityScopeType, string> = {
  factory: 'Factory',
  product_family: 'Product Family',
  line: 'Line',
}

export const CONSTRAINT_TYPE_LABEL: Record<CapacityConstraintType, string> = {
  max_sku_count: 'Max SKU Count',
  max_output_qty: 'Max Output Qty',
}

export const capacityApi = {
  list: () => api.get<CapacityRule[]>('/capacity/rules'),

  create: (body: CapacityRuleBody) => api.post<CapacityRule>('/capacity/rules', body),

  update: (id: string, body: CapacityRuleUpdateBody) =>
    api.patch<CapacityRule>(`/capacity/rules/${id}`, body),

  remove: (id: string) => api.delete<void>(`/capacity/rules/${id}`),
}
