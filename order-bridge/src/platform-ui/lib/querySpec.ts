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
