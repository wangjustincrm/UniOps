// Typed client for mrp-api's /mps/* endpoints (app/api/v1/mps.py, Phase 1B
// Task 4 — design §6.5/§6.6 page 3). Decimal fields (qty, safety_margin_
// fraction, used_qty, max_output_qty) arrive as JSON strings — same
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

// Loosely typed: `stats` is a JSONB column (mps.py's `_compute_stats()`)
// with no server-side schema guarantee beyond "these three keys today".
export interface MpsRunStats {
  line_count?: number
  prebuild_count?: number
  capacity_gap_count?: number
}

export interface MpsLine {
  id: string
  material_code: string
  demand_month: string // 'YYYY-MM'
  plan_month: string // 'YYYY-MM'
  qty: string // Decimal-as-string
  demand_forecast: string // Decimal-as-string
  opening_stock: string // Decimal-as-string
  is_prebuild: boolean
  prebuild_reason: string | null
  shelf_life_ok: boolean
  capacity_gap: boolean
  locked_by_planner: boolean
  manual_adjusted: boolean
  status: string
}

export interface MpsRun {
  id: string
  run_no: string
  forecast_version_id: string
  horizon_start_month: string // 'YYYY-MM'
  horizon_months: number
  status: MpsRunStatus
  safety_margin_fraction: string // Decimal-as-string
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

export interface CapacityOccupancyMonth {
  month: string // 'YYYY-MM'
  used_sku_count: number
  used_qty: string // Decimal-as-string
  max_sku_count: number | null
  max_output_qty: string | null // Decimal-as-string
}

export interface MpsRunGet extends MpsRunDetail {
  capacity_occupancy: CapacityOccupancyMonth[]
}

export interface AdjustLineBody {
  qty?: number
  plan_month?: string // 'YYYY-MM'
  locked_by_planner?: boolean
}

export const mpsApi = {
  /** Requires the forecast version to be status='confirmed' (409 otherwise
   *  — see mps.py's create_run()). `safetyMarginFraction` omitted lets the
   *  backend fall back to its own default (1/3 shelf life). */
  generate: (forecastVersionId: string, safetyMarginFraction?: number) =>
    api.post<MpsRunDetail>('/mps/runs', {
      forecast_version_id: forecastVersionId,
      ...(safetyMarginFraction != null ? { safety_margin_fraction: safetyMarginFraction } : {}),
    }),

  get: (runId: string) => api.get<MpsRunGet>(`/mps/runs/${runId}`),

  /** 409s once the run is released (immutable) — see `_require_not_released`. */
  recalculate: (runId: string) => api.post<MpsRunDetail>(`/mps/runs/${runId}/recalculate`, {}),

  /** Partial update (backend does exclude_unset-equivalent field-by-field
   *  diffing) — any changed qty/plan_month sets manual_adjusted=true
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
