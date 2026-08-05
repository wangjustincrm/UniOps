// Typed client for mrp-api's /forecast/* endpoints (app/api/v1/forecast.py).
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

export interface SkippedCell {
  material_code: string
  month: string
}

export interface CellsUpsertResponse {
  upserted: number
  skipped_frozen: SkippedCell[]
}

export interface ImportRowError {
  row: number
  column: string
  reason: string
}

export interface ImportResponse {
  ok_rows: number
  error_rows: ImportRowError[]
  skipped_frozen: SkippedCell[]
  would_upsert: number
}

export interface CreateVersionBody {
  horizon_start_month: string
  horizon_months?: number
  note?: string | null
  copy_from_version_id?: string | null
}

export const forecastApi = {
  listVersions: (page = 1, pageSize = 100) =>
    api.get<ForecastVersionListResponse>(`/forecast/versions?page=${page}&page_size=${pageSize}`),

  createVersion: (body: CreateVersionBody) =>
    api.post<ForecastVersion>('/forecast/versions', body),

  getGrid: (versionId: string) =>
    api.get<GridResponse>(`/forecast/versions/${versionId}/grid`),

  upsertCells: (versionId: string, cells: Array<{ material_code: string; month: string; qty: number }>) =>
    api.put<CellsUpsertResponse>(`/forecast/versions/${versionId}/cells`, { cells }),

  confirmVersion: (versionId: string) =>
    api.post<ForecastVersion>(`/forecast/versions/${versionId}/confirm`, {}),

  downloadTemplate: (versionId: string) =>
    api.getBlobWithFilename(`/forecast/versions/${versionId}/template`),

  exportGrid: (versionId: string) =>
    api.getBlobWithFilename(`/forecast/versions/${versionId}/export`),

  importForecast: (versionId: string, file: File, dryRun: boolean) => {
    const form = new FormData()
    form.append('file', file)
    return api.postForm<ImportResponse>(
      `/forecast/versions/${versionId}/import?dry_run=${dryRun ? 'true' : 'false'}`,
      form,
    )
  },
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
