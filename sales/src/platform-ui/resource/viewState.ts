import type { Filter, QuerySpec, SortKey } from './querySpec'

// One JSON object holds everything a list view is: the query (filters, sort,
// search, paging) and the layout (column order, hidden columns). Saved views
// per user (design §13.19) store exactly this — so it is versioned, and a
// stored view of another version is dropped rather than misread.

export const VIEW_STATE_VERSION = 1

export interface ViewState {
  version: typeof VIEW_STATE_VERSION
  sort: SortKey[]
  filters: Filter[]
  search?: string
  pageSize: number
  /** Column ids in display order (empty = the page's own order). */
  columns: string[]
  hidden: string[]
}

export interface Layout {
  columns: string[]
  hidden: string[]
}

export const emptyLayout = (): Layout => ({ columns: [], hidden: [] })

export function viewStateOf(spec: QuerySpec, layout: Layout): ViewState {
  return {
    version: VIEW_STATE_VERSION, sort: spec.sort ?? [], filters: spec.filters ?? [],
    ...(spec.search ? { search: spec.search } : {}), pageSize: spec.pageSize, columns: layout.columns, hidden: layout.hidden,
  }
}

/** The query half of a view, on page 1. */
export function specOf(view: ViewState): QuerySpec {
  return { page: 1, pageSize: view.pageSize, sort: view.sort, filters: view.filters, search: view.search }
}

export const serializeView = (view: ViewState): string => JSON.stringify(view)

/** A stored view, or null when it is not one this version can read. */
export function parseView(text: string): ViewState | null {
  try {
    const v = JSON.parse(text) as Partial<ViewState>
    if (v?.version !== VIEW_STATE_VERSION || !Array.isArray(v.sort) || !Array.isArray(v.filters)) return null
    return { columns: [], hidden: [], pageSize: 50, ...v } as ViewState
  } catch {
    return null
  }
}
