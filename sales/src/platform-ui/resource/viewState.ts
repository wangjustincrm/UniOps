import type { Filter, QuerySpec, SortKey } from './querySpec'

// One JSON object holds everything a list view is: the query (filters, sort,
// group, search, paging, history) and the layout (column order and which
// columns show). Saved views (design §13.19) store exactly this — so it is
// versioned, and the server flags a stored view of another version.
//
// Layout: `columns` is the display order and also lists the extra columns
// (fields the page does not show by itself) that are switched on; `hidden`
// lists the page's own columns that are switched off.

export const VIEW_STATE_VERSION = 1

export interface ViewState {
  version: typeof VIEW_STATE_VERSION
  sort: SortKey[]
  group: SortKey[]
  filters: Filter[]
  search?: string
  pageSize: number
  columns: string[]
  hidden: string[]
  /** Auto-refresh every N seconds (absent: off). */
  refresh?: number
}

export interface Layout {
  columns: string[]
  hidden: string[]
  /** Auto-refresh every N seconds (absent: off). */
  refresh?: number
}

/** The auto-refresh choices, in seconds (0 = off). */
export const AUTO_REFRESH = [0, 30, 60, 300] as const

export const emptyLayout = (): Layout => ({ columns: [], hidden: [] })

export function viewStateOf(spec: QuerySpec, layout: Layout): ViewState {
  return normalizeView({
    version: VIEW_STATE_VERSION, sort: spec.sort ?? [], group: spec.group ?? [], filters: spec.filters ?? [],
    search: spec.search, pageSize: spec.pageSize,
    columns: layout.columns, hidden: layout.hidden, refresh: layout.refresh,
  })
}

/** The query half of a view, on page 1. */
export function specOf(view: ViewState): QuerySpec {
  return {
    page: 1, pageSize: view.pageSize, sort: view.sort, group: view.group, filters: view.filters, search: view.search,
  }
}

export const layoutOf = (view: ViewState): Layout => ({ columns: view.columns, hidden: view.hidden,
  ...(view.refresh ? { refresh: view.refresh } : {}) })

/** Every part present and in one shape, so two states compare as text. */
export function normalizeView(v: Partial<ViewState>): ViewState {
  const sortKey = (k: SortKey) => ({ field: k.field, desc: !!k.desc })
  return {
    version: VIEW_STATE_VERSION,
    sort: (v.sort ?? []).map(sortKey),
    group: (v.group ?? []).map(sortKey),
    filters: (v.filters ?? []).map((f) => ({ field: f.field, op: f.op, value: f.value ?? null })),
    ...(v.search?.trim() ? { search: v.search.trim() } : {}),
    pageSize: v.pageSize ?? 50,
    columns: v.columns ?? [],
    hidden: v.hidden ?? [],
    ...(v.refresh ? { refresh: v.refresh } : {}),
  }
}

export const sameView = (a: Partial<ViewState>, b: Partial<ViewState>): boolean =>
  JSON.stringify(normalizeView(a)) === JSON.stringify(normalizeView(b))

export const serializeView = (view: ViewState): string => JSON.stringify(normalizeView(view))

/** A stored view, or null when it is not one this version can read. */
export function parseView(text: string): ViewState | null {
  try {
    const v = JSON.parse(text) as Partial<ViewState>
    if (v?.version !== VIEW_STATE_VERSION || !Array.isArray(v.sort) || !Array.isArray(v.filters)) return null
    return normalizeView(v)
  } catch {
    return null
  }
}
