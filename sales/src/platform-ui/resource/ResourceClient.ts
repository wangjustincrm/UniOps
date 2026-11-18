import { toSearchParams, type Page, type QuerySpec } from './querySpec'
import type { ListMeta } from './listMeta'

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
  /** The list's fields, labels, types and operators (`GET <resource>/meta`). */
  meta: () => Promise<ListMeta>
  /** The values of a field already in the list, for the search panel. */
  suggest: (field: string, query: string) => Promise<string[]>
  get: (id: number) => Promise<TRead>
  create: (data: TCreate) => Promise<TRead>
  /** `data` holds only the fields to change; a field set to null is cleared. */
  update: (id: number, data: Partial<TUpdate>, version?: number) => Promise<TRead>
  deactivate: (id: number) => Promise<TRead>
  /** Every row the query selects, as a file the back end serves under the resource (default its
   *  CSV export; e.g. "report/settlements.pdf"): the list's filters and sort, no paging. */
  exportPath: (spec: QuerySpec, file?: string, columns?: string[]) => string
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
    meta: () => http.get<ListMeta>(`${base}/meta`),
    suggest: (field, query) => http.get<string[]>(`${base}/suggest?${new URLSearchParams({ field, q: query })}`),
    get: (id) => http.get<TRead>(`${base}/${id}`),
    create: (data) => http.post<TRead>(base, data),
    update: (id, data, version) =>
      http.put<TRead>(version === undefined ? `${base}/${id}` : `${base}/${id}?version=${version}`, data),
    deactivate: (id) => http.post<TRead>(`${base}/${id}/deactivate`),
    exportPath: (spec, file = 'export.csv', columns) => {
      const p = toSearchParams(spec)
      p.delete('page')
      p.delete('page_size')
      if (columns?.length) p.set('columns', columns.join(','))  // the columns the user sees, in their order
      const qs = p.toString()
      const path = `${base}/${file.replace(/^\/+/, '')}`
      return qs ? `${path}?${qs}` : path
    },
    importCsv: (file, dryRun) => {
      if (!http.upload) throw new Error('this API client cannot upload files')
      const form = new FormData()
      form.append('file', file)
      return http.upload<ImportReport>(`${base}/import.csv?dry_run=${dryRun}`, form)
    },
  }
}
