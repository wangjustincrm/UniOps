// Typed client for mrp-api's /series/* endpoints (app/api/v1/series.py —
// Continuous Sales Forecast redesign, Task 6). Mirrors forecastApi.ts's
// client structure; the grid shape is identical to /forecast/versions/{id}/grid
// so GridResponse/GridRow are reused from there, not redefined here.
// Decimal fields (qty/total/cells/old_qty/new_qty) arrive as JSON strings —
// this is a project-wide FastAPI/Pydantic gotcha (see
// feedback_uniops_decimal_as_string in project memory), so every numeric
// field below is typed `string` (or `string | null`) at the wire boundary
// and converted with Number() at the call site, never trusted as a number
// here.
import { api } from '@/lib/api'
import type { ForecastVersion, GridResponse } from './forecastApi'

export type { GridResponse, GridRow } from './forecastApi'

export interface CellsUpsertResponse {
  upserted: number
  changed: number
}

export interface ChangeLogItem {
  material_code: string
  month: string
  old_qty: string | null
  new_qty: string | null
  source: string
  changed_by: string | null
  // Write-time-denormalized editor display name (mrp06 follow-up — see
  // mrp-api's app/models/demand_series.py MrpForecastChangeLog.changed_by_name
  // docstring). Null when identity-api was unavailable at write time, or for
  // rows written before mrp06 — CellHistoryPopover must never fall back to
  // rendering `changed_by` (the raw UUID) in that case.
  changed_by_name: string | null
  changed_at: string
}

export interface ChangeLogResponse {
  items: ChangeLogItem[]
}

export const seriesApi = {
  getGrid: (from: string, to: string) =>
    api.get<GridResponse>(`/series?from=${from}&to=${to}`),

  upsertCells: (cells: Array<{ material_code: string; month: string; qty: number }>) =>
    api.put<CellsUpsertResponse>('/series/cells', { cells }),

  getChangeLog: (materialCode: string, month?: string) =>
    api.get<ChangeLogResponse>(
      `/series/change-log?material_code=${encodeURIComponent(materialCode)}${month ? `&month=${month}` : ''}`,
    ),

  generateOutlook: (anchorMonth: string, horizonMonths = 18) =>
    api.post<ForecastVersion>('/series/outlook', { anchor_month: anchorMonth, horizon_months: horizonMonths }),
}
