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
  /** Each row carries it: it can be shown as a column. False: a key to filter or sort on only. */
  in_row?: boolean
  /** An amount of money (declared by the list), in the currency of the row's `currency_field`. */
  money?: boolean
  currency_field?: string | null
  /** Its group in the search panel (the server's declaration; "Other" when the list declares none). */
  group?: string
  /** It names a record with a page of its own: the record's type and the row's field holding its key. */
  ref?: { type: string; key: string }
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
  /** The search panel's groups, in order ("Other" last). */
  groups?: string[]
  /** The periods a date search may name (the server's words). */
  relative_dates?: { value: string; label: string; help: string }[]
  /** Columns only the page has (the server's views may name them beside the fields). */
  client_columns?: string[]
  /** What the list offers, and whether this person may use it (the server still checks each use). */
  capabilities?: ListCapabilities
  /** Per record type the fields name: whether this person may read it (a link is offered only then). */
  refs?: Record<string, { readable: boolean }>
}

export interface ListCapabilities {
  /** Null: the list has no export. `computed`: the page's own columns the export can write. */
  export: { allowed: boolean; computed: { id: string; label: string }[] } | null
  suggest: boolean
  /** A record's audit history. */
  history?: boolean
  /** A person's files on a record; null: none. `allowed`: this person may attach and remove. */
  attachments?: { type: string; allowed: boolean; max_bytes: number; extensions: string[] } | null
  /** What a record (or the list as it is filtered) gives as a file. */
  files?: { path: string; media_type: string; over: 'record' | 'list'; allowed?: boolean }[]
  actions: { id: string; allowed: boolean; label?: string; bulk?: boolean; reason?: string; confirm?: string | null; sends?: string | null }[]
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

/** What the server says an action sends, if anything (its `sends` in /meta): shown on its button and in its confirmation. */
export function useActionSends(client: { basePath: string; meta: () => Promise<ListMeta> }, actionId: string): string | undefined {
  return useListMeta(client).data?.capabilities?.actions.find((a) => a.id === actionId)?.sends ?? undefined
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
