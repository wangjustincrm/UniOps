// Typed client for mrp-api's /mps/* endpoints (app/api/v1/mps.py — design
// §2.0/§6.5, weekly since the 2026-08-12 rework). Decimal fields (qty,
// safety_margin_fraction, used_qty, max_output_qty) arrive as JSON strings — same
// project-wide FastAPI/Pydantic gotcha forecastApi.ts and capacityApi.ts
// document (feedback_uniops_decimal_as_string in project memory) — so every
// numeric field below is typed `string` at the wire boundary and converted
// with Number() at the call site, never trusted as a number here.
//
// Permission keys (mps.py's module docstring): `mrp.run.execute` gates
// generate/recalculate/line-edit; `mrp.report.view` gates the read;
// `mrp.proposal.confirm` gates release.
import { api } from '@/lib/api'

// The model reserves 'confirmed' but no endpoint in mps.py ever produces it
// (see that module's docstring) — kept in the type anyway so a value that
// slips through some future path still narrows instead of falling to
// `string`, and StatusBadge renders any unrecognized value neutrally.
export type MpsRunStatus = 'draft' | 'confirmed' | 'released'

/** One intent-product row (planned SKU with no ERP material code yet, see
 *  forecast/intentApi.ts) that generate()/recalculate() left out of `lines`
 *  entirely — MPS never schedules these. `qty` is Decimal-as-string, same
 *  wire convention as everything else in this file. */
export interface MpsSkippedIntent {
  code: string
  /** NULLABLE on the wire. The backend emits `ForecastLine.intent_name`
   *  (mps.py's `_skipped_intent_stats`), a nullable column — every path
   *  that writes it fills it in today, but nothing in the schema or the
   *  query enforces that, and `stats` is an unvalidated JSONB blob besides.
   *  Declaring it `string` made TypeScript vouch for a guarantee the server
   *  does not give; a null would then have rendered as a nameless
   *  "Not scheduled (intent):  · 700 kg". Render sites must fall back to
   *  `code`. */
  name: string | null
  qty: string
}

// Loosely typed: `stats` is a JSONB column (mps.py's `_compute_stats()`)
// with no server-side schema guarantee beyond "these keys today".
export interface MpsRunStats {
  line_count?: number
  /** Lines whose production crossed into an EARLIER month than the demand's
   *  own bucket. NOT "lines placed earlier than their target week" — that is
   *  `MpsLine.weeks_early`, and it is true of most lines of a healthy plan
   *  (levelling across a month is the point). */
  prebuild_count?: number
  capacity_gap_count?: number
  skipped_intent?: MpsSkippedIntent[]
  /** design §7: products this run planned with NO shelf life on record.
   *  The engine still schedules them, but refuses to move them a single week
   *  early (fail safe), which from the outside looks exactly like capacity
   *  pressure. ERP's `exp` field has never been verified to hold values for
   *  finished goods, so the generate summary names them. `name` is nullable
   *  on the same degrade-to-null contract as `MpsSkippedIntent.name`. */
  no_shelf_life?: MpsMissingShelfLife[]
}

export interface MpsMissingShelfLife {
  code: string
  name: string | null
}

export interface MpsLine {
  id: string
  material_code: string
  demand_month: string // 'YYYY-MM'
  /** The week production is scheduled in, as an ISO date ('2026-09-28') on
   *  the RUN's own week grid — not the current planning parameter's. */
  plan_week_start: string
  /** The month `plan_week_start` belongs to under the run's week mode
   *  ('YYYY-MM'). Denormalized server-side: an ISO week can start in one
   *  calendar month and belong to the next, so this is NOT
   *  `plan_week_start.slice(0, 7)`. */
  plan_week_month: string
  /** Rendered label for `plan_week_start` under the run's mode, e.g.
   *  '2026-W40 · Sep 28–Oct 4' or 'Sep W4 · Sep 22–28'. */
  week_label: string
  /** Whole weeks earlier than this line's lead-shifted target week.
   *  **This is the field that answers "is this line early", not
   *  `is_prebuild`.** */
  weeks_early: number
  qty: string // Decimal-as-string
  demand_forecast: string // Decimal-as-string
  opening_stock: string // Decimal-as-string
  /** Production pulled into an EARLIER MONTH than the demand's own bucket
   *  month — a genuinely cross-month pre-build, worth an amber warning.
   *  Producing early WITHIN the demand's own month is ordinary levelling and
   *  is deliberately NOT flagged here: flagging it made 87% of the lines of
   *  a gap-free plan read "pre-built", which is wallpaper. To ask "did this
   *  land before its target week", read `weeks_early > 0`. */
  is_prebuild: boolean
  prebuild_reason: string | null
  shelf_life_ok: boolean
  capacity_gap: boolean
  /** True when the engine could not push production back a full
   *  `production_lead_weeks` before the demand month (the target week was
   *  clamped to the current week). A line can be a shortfall without being
   *  a capacity_gap: shortfall means "produced later than the lead asked
   *  for", gap means "demand unmet even after that". See
   *  ProductionMatrix.tsx for how the two combine on a cell (gap takes
   *  precedence). */
  lead_shortfall: boolean
  locked_by_planner: boolean
  manual_adjusted: boolean
  status: string
  // Minimum lot size (mrp11) — see MpsLineLotFields below for what each
  // one means. Decimals arrive as strings; wrap in Number().
  surplus_qty: string
  carry_in_qty: string
  covered_by_carry: boolean
  late_production: boolean
  surplus_expiry_risk: boolean
  below_min_lot: boolean
}

export interface MpsRun {
  id: string
  run_no: string
  forecast_version_id: string
  horizon_start_month: string // 'YYYY-MM'
  horizon_months: number
  status: MpsRunStatus
  safety_margin_fraction: string // Decimal-as-string
  /** How many WEEKS earlier than a demand month's last week the engine
   *  tries to schedule production for it (0–52, default 4; set at
   *  generate() time — see `generate()`'s opts below). Snapshotted on the
   *  run: recalculate reuses this, it is never re-read from settings. */
  production_lead_weeks: number
  /** Which week-boundary convention this run was GENERATED under
   *  ('iso_thursday' | 'iso_first_day' | 'month_fixed'). Snapshotted at
   *  generate time: changing the factory-wide parameter afterwards does not
   *  reshape or relabel an existing run (design §5.4). Render this run's
   *  weeks with `week_label`, never by re-deriving from today's setting. */
  week_calendar_mode: string
  /** Which weekday this run's weeks begin on (0=Monday .. 6=Sunday). Also a
   *  generate-time snapshot: this factory plans Saturday-start weeks, and
   *  switching the setting must not redraw the columns of a plan already
   *  released. Never re-derive a week's day from today's setting. */
  week_start_dow: number
  /** Frozen zone: months whose materials are already purchased, inherited
   *  from the plan in force and not re-planned. `frozen_months` is what was
   *  actually applied (0 for a first run, which has nothing to inherit) and
   *  `frozen_until_month` is the last month it covers, or null. The API
   *  refuses to adjust a line inside it; the matrix greys those columns. */
  frozen_months: number
  frozen_until_month: string | null
  generated_by: string | null
  stats: MpsRunStats | null
}

// POST /runs, POST .../recalculate and POST .../confirm-release all return
// this shape (MpsRunDetailResponse server-side) — run fields + lines, no
// capacity_occupancy (that's computed fresh only by GET .../{id}, see
// mps.py's module docstring: "GET /runs/{id} is more precise... a
// read-only report, not a scheduling input"). Callers that need occupancy
// after a mutation should refetch via `get()`.
export interface MpsRunDetail extends MpsRun {
  lines: MpsLine[]
}

/** Per-WEEK occupancy: what the plan books in a week vs. the limits in
 *  force for that week, week exceptions (a maintenance week is
 *  `max_output_qty: '0'`) included. Recomputed on every GET, never stored —
 *  so it always answers "am I over the ceiling I have today". */
export interface CapacityOccupancyWeek {
  week_start: string // ISO date, the week's start
  week_month: string // 'YYYY-MM', the week's owning month
  week_label: string
  used_sku_count: number
  used_qty: string // Decimal-as-string
  max_sku_count: number | null
  max_output_qty: string | null // Decimal-as-string
}

/** One column of the run's time axis — mrp-api's `_compute_week_grid`
 *  (`app/api/v1/mps.py`), mirroring `mps_export.py`'s own `week_grid`
 *  construction byte-for-byte so the on-screen matrix and the xlsx export
 *  can never disagree on which weeks exist. Spans the run's declared
 *  horizon UNION every month a line actually landed in (a lead-shifted
 *  pre-build can land just before the nominal horizon start), walked
 *  contiguously so no month between them is skipped.
 *
 *  **This — not the lines themselves — is the authoritative "which weeks
 *  exist" list.** A week with zero lines (a maintenance week is
 *  `max_output_qty: '0'` and has zero lines BY CONSTRUCTION) still gets an
 *  entry here; deriving columns from `lines` alone would silently drop
 *  that week's header — see ProductionMatrix.tsx's `weekColumns.ts`
 *  usage. */
export interface WeekGridEntry {
  week_start: string // ISO date
  week_month: string // 'YYYY-MM', the week's owning month
  label: string
}

export interface MpsRunGet extends MpsRunDetail {
  capacity_occupancy: CapacityOccupancyWeek[]
  week_grid: WeekGridEntry[]
}

export interface AdjustLineBody {
  qty?: number
  /** Move the line to a different week: an ISO date that must be a week
   *  START on the run's own grid (422 otherwise). The server re-derives
   *  `plan_week_month`/`weeks_early` and re-runs the shelf-life check, and
   *  422s a week the shelf life does not allow rather than storing it. */
  plan_week_start?: string
  /** 422 on a line with `capacity_gap: true` — a shortfall is not committed
   *  production, and the engine drops locked gap lines on recalculate. */
  locked_by_planner?: boolean
}

export const mpsApi = {
  /** Requires the forecast version to be status='confirmed' (409 otherwise
   *  — see mps.py's create_run()). Both `opts` fields are optional and
   *  independently omittable: `safety_margin_fraction` omitted falls back
   *  to the backend's own default (1/3 shelf life); `production_lead_weeks`
   *  omitted falls back to the backend's default of 4. There is no
   *  `week_calendar_mode` option — the mode is a factory-wide setting
   *  (`PUT /params/week_calendar_mode`), and the run records whichever one
   *  was in force. */
  generate: (
    forecastVersionId: string,
    opts?: { safety_margin_fraction?: number; production_lead_weeks?: number },
  ) =>
    api.post<MpsRunDetail>('/mps/runs', {
      forecast_version_id: forecastVersionId,
      ...(opts?.safety_margin_fraction != null ? { safety_margin_fraction: opts.safety_margin_fraction } : {}),
      ...(opts?.production_lead_weeks != null ? { production_lead_weeks: opts.production_lead_weeks } : {}),
    }),

  get: (runId: string) => api.get<MpsRunGet>(`/mps/runs/${runId}`),

  /** 409s once the run is released (immutable) — see `_require_not_released`. */
  recalculate: (runId: string) => api.post<MpsRunDetail>(`/mps/runs/${runId}/recalculate`, {}),

  /** Partial update (backend does exclude_unset-equivalent field-by-field
   *  diffing) — any changed qty/plan_week_start sets manual_adjusted=true
   *  server-side. 409s once the run is released. */
  adjustLine: (runId: string, lineId: string, body: AdjustLineBody) =>
    api.patch<MpsLine>(`/mps/runs/${runId}/lines/${lineId}`, body),

  /** Materializes non-gap lines into mrp_demands and flips the run to
   *  'released' (immutable from then on). 409s if already released. */
  confirmRelease: (runId: string) => api.post<MpsRunDetail>(`/mps/runs/${runId}/confirm-release`, {}),

  /** xlsx blob (unit converts qty columns kg<->t server-side) — caller (T5)
   *  hands this to forecastApi's `saveBlob` pattern to trigger a download. */
  exportRun: (runId: string, unit: 'kg' | 't') =>
    api.getBlob(`/mps/runs/${runId}/export?unit=${unit}`),
}
