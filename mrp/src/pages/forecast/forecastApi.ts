// Typed client for mrp-api's /forecast/* read/export endpoints
// (app/api/v1/forecast.py). The write/draft endpoints this client used to
// wrap (createVersion/upsertCells/confirmVersion/importForecast) were
// retired in the Continuous Sales Forecast redesign (Task 8) — editing now
// happens against the living series (see seriesApi.ts). This module keeps
// only what's still read here: the version list (Production Plan's forecast
// picker), a version's grid, and template/export downloads. GridResponse/
// GridRow/ForecastVersion are also re-exported by seriesApi.ts (identical
// shape) — do not remove them even though nothing in this file's own
// exported functions still returns a bare ForecastVersion besides
// listVersions.
// Decimal fields (qty/total/column_totals/grand_total) arrive as JSON
// strings — this is a project-wide FastAPI/Pydantic gotcha (see
// feedback_uniops_decimal_as_string in project memory), so every numeric
// field below is typed `string` at the wire boundary and converted with
// Number() at the call site, never trusted as a number here.
import { api } from '@/lib/api'

export type ForecastVersionStatus = 'draft' | 'confirmed' | 'superseded'

export interface ForecastVersion {
  id: string
  version_no: string
  status: ForecastVersionStatus
  horizon_start_month: string
  horizon_months: number
  note: string | null
  created_by: string | null
  confirmed_at: string | null
  created_at: string
  updated_at: string
}

export interface ForecastVersionListResponse {
  items: ForecastVersion[]
  total: number
  page: number
  page_size: number
}

export interface GridRow {
  material_code: string
  name: string | null
  cells: Record<string, string> // month -> Decimal-as-string
  total: string
}

export interface GridResponse {
  months: string[]
  rows: GridRow[]
  column_totals: Record<string, string>
  grand_total: string
}

export const forecastApi = {
  listVersions: (page = 1, pageSize = 100) =>
    api.get<ForecastVersionListResponse>(`/forecast/versions?page=${page}&page_size=${pageSize}`),

  getGrid: (versionId: string) =>
    api.get<GridResponse>(`/forecast/versions/${versionId}/grid`),

  downloadTemplate: (versionId: string) =>
    api.getBlobWithFilename(`/forecast/versions/${versionId}/template`),

  exportGrid: (versionId: string) =>
    api.getBlobWithFilename(`/forecast/versions/${versionId}/export`),
}

/** Trigger a browser download of a blob with the given filename (or a fallback). */
export function saveBlob(blob: Blob, filename: string | null, fallback: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename ?? fallback
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
