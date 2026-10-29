// The list contract of the back-end platform (app/platform/query.py):
//   ?page=&page_size=&sort=a,-b&search=&filter=field:op:value&include_historical=
// `in` values are joined with "|", `range` is "low..high" (either side empty).

export type FilterOp = 'eq' | 'in' | 'contains' | 'range'

export interface SortKey {
  field: string
  desc?: boolean
}

export type FilterValue = string | string[] | [string | null, string | null]

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

function filterValue(f: Filter): string {
  if (f.op === 'in') return (f.value as string[]).join('|')
  if (f.op === 'range') {
    const [low, high] = f.value as [string | null, string | null]
    return `${low ?? ''}..${high ?? ''}`
  }
  return String(f.value)
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
