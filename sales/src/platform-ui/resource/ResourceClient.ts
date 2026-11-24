import { toSearchParams, type Page, type QuerySpec } from './querySpec'
import type { ListMeta } from './listMeta'

/** What the business API client provides. Paths are relative to its base. */
export interface HttpClient {
  get: <T>(path: string) => Promise<T>
  post: <T>(path: string, body?: unknown) => Promise<T>
  put: <T>(path: string, body: unknown) => Promise<T>
  /** multipart upload — needed for CSV import only */
  upload?: <T>(path: string, form: FormData) => Promise<T>
  /** Saves an authenticated GET as a file — needed for exports only. The name is the server's, else `name`;
   *  `own`: always `name`. */
  download?: (path: string, name: string, own?: boolean) => Promise<void>
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
  /** Saves the CSV of every row the query selects (its name is the server's, unless the page gives one). */
  exportCsv: (spec: QuerySpec, columns?: string[], name?: string) => Promise<void>
  importCsv: (file: File, dryRun: boolean) => Promise<ImportReport>
  /** A record action on one record or many (`POST <resource>/actions/<id>`, platform/bulk_actions.py). */
  action: (actionId: string, body: ActionRunBody) => Promise<ActionOut>
}

/** What a record action is run on: {id: version} as the person saw them, or "all matching" (the list's query
 *  string, with how many the person was told match). `dry_run`: what would happen, nothing written. */
export interface ActionRunBody {
  ids?: Record<string, number>
  query?: string
  expected?: number
  input?: unknown
  dry_run?: boolean
}
export interface ActionResultRow { id: number; status: 'done' | 'skipped' | 'failed'; reason?: string | null; version?: number | null }
export interface ActionOut { action: string; dry_run: boolean; results: ActionResultRow[]; counts: Record<'done' | 'skipped' | 'failed', number> }

/** A client for one resource served by the back end's `crud_router`. The
 *  types come from the business code (generated from the OpenAPI schema). */
export function createResourceClient<TRead, TCreate, TUpdate = TCreate>(
  http: HttpClient,
  basePath: string,
): ResourceClient<TRead, TCreate, TUpdate> {
  const base = basePath.replace(/\/$/, '')
  const exportPath = (spec: QuerySpec, file = 'export.csv', columns?: string[]): string => {
    const p = toSearchParams(spec)
    p.delete('page')
    p.delete('page_size')
    if (columns?.length) p.set('columns', columns.join(','))  // the columns the user sees, in their order
    const qs = p.toString()
    const path = `${base}/${file.replace(/^\/+/, '')}`
    return qs ? `${path}?${qs}` : path
  }
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
    exportPath,
    exportCsv: (spec, columns, name) => {
      if (!http.download) throw new Error('this API client cannot download files')
      const fallback = `${base.replace(/^\/+/, '').replace(/\//g, '-')}.csv`
      return http.download(exportPath(spec, undefined, columns), name ?? fallback, name !== undefined)
    },
    action: (actionId, body) => http.post<ActionOut>(`${base}/actions/${actionId}`, body),
    importCsv: (file, dryRun) => {
      if (!http.upload) throw new Error('this API client cannot upload files')
      const form = new FormData()
      form.append('file', file)
      return http.upload<ImportReport>(`${base}/import.csv?dry_run=${dryRun}`, form)
    },
  }
}
