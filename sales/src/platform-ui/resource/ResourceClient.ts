import { toSearchParams, type Page, type QuerySpec } from './querySpec'

/** What the business API client provides. Paths are relative to its base. */
export interface HttpClient {
  get: <T>(path: string) => Promise<T>
  post: <T>(path: string, body?: unknown) => Promise<T>
  put: <T>(path: string, body: unknown) => Promise<T>
  /** multipart upload — needed for CSV import only */
  upload?: <T>(path: string, form: FormData) => Promise<T>
}

export interface RowIssue {
  row: number
  column: string | null
  code: string
  message: string
}

export interface RowPlan {
  row: number
  key: string
  action: 'create' | 'update' | 'unchanged'
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}

export interface ImportReport {
  ok: boolean
  dry_run: boolean
  written: boolean
  errors: RowIssue[]
  rows: RowPlan[]
  counts: Record<'create' | 'update' | 'unchanged', number>
}

export interface ResourceClient<TRead, TCreate, TUpdate> {
  basePath: string
  list: (spec: QuerySpec) => Promise<Page<TRead>>
  get: (id: number) => Promise<TRead>
  create: (data: TCreate) => Promise<TRead>
  /** `data` holds only the fields to change; a field set to null is cleared. */
  update: (id: number, data: Partial<TUpdate>, version?: number) => Promise<TRead>
  deactivate: (id: number) => Promise<TRead>
  exportPath: (spec: QuerySpec) => string
  importCsv: (file: File, dryRun: boolean) => Promise<ImportReport>
}

/** A client for one resource served by the back end's `crud_router`. The
 *  types come from the business code (generated from the OpenAPI schema). */
export function createResourceClient<TRead, TCreate, TUpdate = TCreate>(
  http: HttpClient,
  basePath: string,
): ResourceClient<TRead, TCreate, TUpdate> {
  const base = basePath.replace(/\/$/, '')
  return {
    basePath: base,
    list: (spec) => http.get<Page<TRead>>(`${base}?${toSearchParams(spec)}`),
    get: (id) => http.get<TRead>(`${base}/${id}`),
    create: (data) => http.post<TRead>(base, data),
    update: (id, data, version) =>
      http.put<TRead>(version === undefined ? `${base}/${id}` : `${base}/${id}?version=${version}`, data),
    deactivate: (id) => http.post<TRead>(`${base}/${id}/deactivate`),
    exportPath: (spec) => {
      const p = toSearchParams(spec)
      p.delete('page')
      p.delete('page_size')
      const qs = p.toString()
      return qs ? `${base}/export.csv?${qs}` : `${base}/export.csv`
    },
    importCsv: (file, dryRun) => {
      if (!http.upload) throw new Error('this API client cannot upload files')
      const form = new FormData()
      form.append('file', file)
      return http.upload<ImportReport>(`${base}/import.csv?dry_run=${dryRun}`, form)
    },
  }
}
