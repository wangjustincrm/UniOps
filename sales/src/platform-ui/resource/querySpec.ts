// The list query contract of the back-end platform (app/platform/query.py,
// design §13.19):
//   ?page=&page_size=&sort=a,-b&search=&filter=field:op:value&include_historical=
// Several values are joined with "|" (a literal | is \|, a backslash \\):
// any of them for the positive operators, none of them for ne / not_contains
// / not_in. between (old name range) is "low..high", either side empty;
// is_null / not_null take no value. Different filters are AND.

export type FilterOp =
  | 'eq' | 'ne' | 'contains' | 'not_contains' | 'starts_with' | 'ends_with' | 'in' | 'not_in'
  | 'gt' | 'gte' | 'lt' | 'lte' | 'between' | 'range' | 'is_null' | 'not_null'

export const NO_VALUE_OPS: readonly FilterOp[] = ['is_null', 'not_null']
export const RANGE_OPS: readonly FilterOp[] = ['between', 'range']

export interface SortKey {
  field: string
  desc?: boolean
}

export type FilterValue = string | string[] | [string | null, string | null] | null

export interface Filter {
  field: string
  op: FilterOp
  value: FilterValue
}

export interface QuerySpec {
  page: number
  pageSize: number
  sort?: SortKey[]
  search?: string
  filters?: Filter[]
  includeHistorical?: boolean
}

export interface Page<T> {
  items: T[]
  total: number
}

export const MAX_PAGE_SIZE = 200

export const defaultSpec = (pageSize = 50): QuerySpec => ({ page: 1, pageSize })

/** A page size the back end accepts (1…MAX_PAGE_SIZE). */
export const clampPageSize = (n: number): number => Math.min(MAX_PAGE_SIZE, Math.max(1, Math.floor(n) || 1))

// ── filter adapter: one filter per field; every change goes back to page 1 ──

export function getFilter(spec: QuerySpec, field: string): Filter | undefined {
  return spec.filters?.find((f) => f.field === field)
}

function isEmpty(value: FilterValue | null | undefined): boolean {
  if (value == null) return true
  if (Array.isArray(value)) return value.every((v) => v == null || v === '')
  return value === ''
}

/** Set (or, with an empty value, remove) the filter on `field`. */
export function setFilter(spec: QuerySpec, field: string, op: FilterOp, value: FilterValue | null | undefined): QuerySpec {
  const rest = (spec.filters ?? []).filter((f) => f.field !== field)
  const filters = isEmpty(value) ? rest : [...rest, { field, op, value: value as FilterValue }]
  return { ...spec, page: 1, filters }
}

export const clearFilter = (spec: QuerySpec, field: string): QuerySpec => setFilter(spec, field, 'eq', null)

/** One value with `\` and `|` escaped, so it survives the `|` join. */
export const escapeValue = (v: string): string => v.replace(/\\/g, '\\\\').replace(/\|/g, '\\|')

export function filterValue(f: Filter): string {
  if (NO_VALUE_OPS.includes(f.op)) return ''
  if (RANGE_OPS.includes(f.op)) {
    const [low, high] = (f.value ?? [null, null]) as [string | null, string | null]
    return `${low ?? ''}..${high ?? ''}`
  }
  const values = Array.isArray(f.value) ? (f.value as string[]) : [String(f.value ?? '')]
  return values.map(escapeValue).join('|')
}

// ── several filters per field (the search panel); every change → page 1 ────

export const filtersOf = (spec: QuerySpec, field: string): Filter[] => (spec.filters ?? []).filter((f) => f.field === field)

/** Replace every filter on `field` (an empty list removes them). */
export function setFieldFilters(spec: QuerySpec, field: string, filters: Filter[]): QuerySpec {
  return { ...spec, page: 1, filters: [...(spec.filters ?? []).filter((f) => f.field !== field), ...filters] }
}

export function removeFilterAt(spec: QuerySpec, index: number): QuerySpec {
  return { ...spec, page: 1, filters: (spec.filters ?? []).filter((_, i) => i !== index) }
}

// ── multi-sort: the order of the keys is the priority ───────────────────────

/** Sort by `field` in the direction given: a key already there keeps its place. */
export function sortBy(spec: QuerySpec, field: string, desc: boolean): QuerySpec {
  const sort = spec.sort ?? []
  const next = sort.some((s) => s.field === field)
    ? sort.map((s) => (s.field === field ? { field, desc } : s))
    : [...sort, { field, desc }]
  return { ...spec, page: 1, sort: next }
}

export function flipSort(spec: QuerySpec, field: string): QuerySpec {
  return { ...spec, page: 1, sort: (spec.sort ?? []).map((s) => (s.field === field ? { field, desc: !s.desc } : s)) }
}

export function removeSort(spec: QuerySpec, field: string): QuerySpec {
  return { ...spec, page: 1, sort: (spec.sort ?? []).filter((s) => s.field !== field) }
}

export function toSearchParams(spec: QuerySpec): URLSearchParams {
  const p = new URLSearchParams()
  p.set('page', String(spec.page))
  p.set('page_size', String(Math.min(spec.pageSize, MAX_PAGE_SIZE)))
  if (spec.sort?.length) p.set('sort', spec.sort.map((s) => (s.desc ? `-${s.field}` : s.field)).join(','))
  if (spec.search?.trim()) p.set('search', spec.search.trim())
  for (const f of spec.filters ?? []) p.append('filter', `${f.field}:${f.op}:${filterValue(f)}`)
  if (spec.includeHistorical) p.set('include_historical', 'true')
  return p
}
