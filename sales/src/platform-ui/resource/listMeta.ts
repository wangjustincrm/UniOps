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
