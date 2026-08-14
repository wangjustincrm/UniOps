// Typed client for mrp-api's /capacity/rules + /capacity/exceptions CRUD and
// /params (app/api/v1/capacity.py + app/api/v1/params.py — Phase 1B Task 1,
// weekly-planning Tasks 2/3, design §5.4). Plain master-data CRUD: GET is
// gated by `mrp.report.view`, POST/PATCH/DELETE by `mrp.param.write` — pages
// (CapacityRulesPage.tsx, WeekExceptionsSection.tsx, WeekDrawer.tsx)
// hide/disable the write actions when the caller only has the read
// permission rather than showing a button that 403s.
//
// `limit_value` is Numeric(18,3) server-side; FastAPI/Pydantic serializes
// Decimal as a JSON string, never a float (feedback_uniops_decimal_as_string
// in project memory) — typed `string` on the wire here, `Number()`'d at the
// call site, never trusted as a number in this file.
import { api } from '@/lib/api'

export type CapacityScopeType = 'factory' | 'product_family' | 'line'
export type CapacityConstraintType = 'max_sku_count' | 'max_output_qty' | 'min_output_qty'

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

// Weekly since the 2026-08-12 rework (design §5.4) — these used to read
// "Max SKU Count" / "Max Output Qty" with no time unit at all, which is
// exactly the ambiguity mrp10b's migration deactivated every rule to force
// a re-entry past: a monthly ceiling looks identical to a weekly one in a
// bare number, and reading one as the other is a silent ~4x capacity error
// (see app/services/capacity.py's module docstring on the same repo side).
export const CONSTRAINT_TYPE_LABEL: Record<CapacityConstraintType, string> = {
  max_sku_count: 'Max SKUs / week',
  max_output_qty: 'Max output / week',
  min_output_qty: 'Min output / week',
}

export interface CapacityException {
  id: string
  week_start: string // ISO date
  scope_type: CapacityScopeType
  scope_ref: string | null
  constraint_type: CapacityConstraintType
  limit_value: string // Decimal-as-string
  uom: string | null
  reason: string | null
  is_active: boolean
}

export interface CapacityExceptionBody {
  week_start: string
  scope_type: CapacityScopeType
  scope_ref: string | null
  constraint_type: CapacityConstraintType
  limit_value: number
  uom: string | null
  reason: string | null
  is_active: boolean
}

// PATCH accepts a partial update (backend does `exclude_unset`) — unlike
// CapacityRuleUpdateBody, callers here genuinely send partial bodies (e.g.
// WeekDrawer's deactivate-only PATCH), so this is Partial, not a full-body
// alias.
export type CapacityExceptionUpdateBody = Partial<CapacityExceptionBody>

/** Find the one exception row (active or not) matching a
 *  (week_start, scope_type, scope_ref, constraint_type) key, out of an
 *  already-fetched list.
 *
 *  This is not a display convenience — it is the write-time upsert rule.
 *  `MrpCapacityException`'s DB schema (app/models/capacity.py) carries a
 *  unique constraint on that exact 4-tuple PLUS a partial unique index for
 *  the factory-wide (`scope_ref IS NULL`) case, and that docstring says
 *  outright: "at most one row (active or not) can ever exist for a given
 *  (week, scope, constraint_type)". So creating a NEW exception for a slot
 *  that already has one — even a deactivated one — 409s
 *  (`create_exception`'s IntegrityError handler in capacity.py). Every
 *  write path in this app (WeekDrawer.tsx, WeekExceptionsSection.tsx) must
 *  call this first and PATCH the result if found, POST only if not. */
export function findExistingException(
  exceptions: CapacityException[],
  key: { week_start: string; scope_type: CapacityScopeType; scope_ref: string | null; constraint_type: CapacityConstraintType },
): CapacityException | null {
  return exceptions.find((e) =>
    e.week_start === key.week_start &&
    e.scope_type === key.scope_type &&
    (e.scope_ref ?? null) === (key.scope_ref ?? null) &&
    e.constraint_type === key.constraint_type,
  ) ?? null
}

// Planning parameters (`mrp_planning_params`, app/api/v1/params.py) — a
// flat key-value store. `week_calendar_mode` is the only writable key today
// (Phase 1C's loss-rate params register into the same backend whitelist
// later); GET returns every row flattened to `{key: value}`, so this file
// types it loosely rather than assuming a fixed key set the backend itself
// doesn't assume either.
export type WeekCalendarMode = 'iso_thursday' | 'iso_first_day' | 'month_fixed'
export const WEEK_CALENDAR_MODES: WeekCalendarMode[] = ['iso_thursday', 'iso_first_day', 'month_fixed']

// Mirrors week_calendar.py's module docstring one-line-per-mode — kept as
// plain user-facing copy here rather than fetched from the backend (there
// is no endpoint that serves prose), and worded to match that docstring's
// own description so the two never drift into contradicting each other.
export const WEEK_CALENDAR_MODE_DESCRIPTION: Record<WeekCalendarMode, string> = {
  iso_thursday: 'Monday-start ISO weeks. A week straddling a month boundary belongs to whichever month contains its Thursday.',
  iso_first_day: 'Monday-start ISO weeks (same boundaries as ISO / Thursday). A straddling week instead belongs to the newer month whenever that week contains that month’s first day.',
  month_fixed: 'Weeks start on the 1st of each month and step by 7 days; weeks never straddle a month boundary, and the last "week" of a month may be a short 1–7 day remainder.',
}

/** 0=Monday .. 6=Sunday, matching Python's `date.weekday()` — the value
 *  stored in `mrp_planning_params.week_start_dow`. This factory's week runs
 *  Saturday to Friday, i.e. 5. */
export type WeekStartDow = 0 | 1 | 2 | 3 | 4 | 5 | 6
export const WEEK_START_DOWS: WeekStartDow[] = [0, 1, 2, 3, 4, 5, 6]
export const WEEK_START_DOW_LABEL: Record<WeekStartDow, string> = {
  0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday',
  4: 'Friday', 5: 'Saturday', 6: 'Sunday',
}

/** `PUT /params/week_start_dow` answers with the saved value plus how many
 *  capacity exceptions (maintenance weeks) were re-keyed onto the new grid —
 *  they are stored by exact week-start date, so a grid change strands them
 *  unless they move with it. */
export interface WeekStartDowUpdateResult {
  week_start_dow: number
  exceptions_shifted: number
}

export const capacityApi = {
  list: () => api.get<CapacityRule[]>('/capacity/rules'),

  create: (body: CapacityRuleBody) => api.post<CapacityRule>('/capacity/rules', body),

  update: (id: string, body: CapacityRuleUpdateBody) =>
    api.patch<CapacityRule>(`/capacity/rules/${id}`, body),

  remove: (id: string) => api.delete<void>(`/capacity/rules/${id}`),

  listExceptions: () => api.get<CapacityException[]>('/capacity/exceptions'),

  createException: (body: CapacityExceptionBody) => api.post<CapacityException>('/capacity/exceptions', body),

  updateException: (id: string, body: CapacityExceptionUpdateBody) =>
    api.patch<CapacityException>(`/capacity/exceptions/${id}`, body),

  getParams: () => api.get<Record<string, unknown>>('/params'),

  setParam: (key: string, value: unknown) => api.put<Record<string, unknown>>(`/params/${key}`, { value }),

  setWeekStartDow: (dow: WeekStartDow) =>
    api.put<WeekStartDowUpdateResult>('/params/week_start_dow', { value: dow }),
}
