import { useQuery } from '@tanstack/react-query'
import type { FilterOp } from './querySpec'
import type { ViewState } from './viewState'

// `GET <resource>/meta` (the back end's ReadOnlyService.meta, design §13.19): built
// on the server from the same declaration the list query uses, so a new
// resource gets its column menus and search panel without UI code.

export type FieldKind = 'text' | 'number' | 'date' | 'datetime' | 'enum' | 'bool'

export interface FieldMeta {
  name: string
  label: string
  type: FieldKind
  enum_values: string[]
  filterable: boolean
  sortable: boolean
  groupable: boolean
  operators: FilterOp[]
  /** Summed in the list's `totals` (the footer and group headers). */
  summable?: boolean
  /** Values of other records are offered while typing (`GET <resource>/suggest`). */
  suggestable?: boolean
}

/** A system view: declared with the list on the server, not editable. */
export interface SystemView {
  key: string
  name: string
  /** A partial ViewState (what the view sets; the rest is the default). */
  state: Partial<ViewState> & { version: number }
}

export interface ListMeta {
  version: number
  fields: FieldMeta[]
  searchable: boolean
  default_sort: string[]
  views?: SystemView[]
  /** Columns only the page has (the server's views may name them beside the fields). */
  client_columns?: string[]
}

/** What a page and its server disagree on about column ids (2026-11-10): the system views' ids that
 *  are neither a field nor a page column, and declared client columns the page lacks. Empty: agreed. */
export function columnMismatch(meta: ListMeta, pageIds: string[]): string[] {
  const page = new Set(pageIds)
  const fields = new Set(meta.fields.map((f) => f.name))
  const named = new Set((meta.views ?? []).flatMap((v) => [...(v.state.columns ?? []), ...(v.state.hidden ?? [])]))
  return [
    ...[...named].filter((id) => !page.has(id) && !fields.has(id)).map((id) => `a system view names "${id}", which the page does not have`),
    ...(meta.client_columns ?? []).filter((id) => !page.has(id)).map((id) => `the server expects the page column "${id}"`),
  ]
}

/** The list's metadata; fetched once per resource (it changes with a release). */
export function useListMeta(source: { basePath: string; meta: () => Promise<ListMeta> } | undefined) {
  return useQuery({
    queryKey: ['platform-meta', source?.basePath],
    queryFn: () => source!.meta(),
    enabled: !!source,
    staleTime: Infinity,
  })
}
