import type { SortingState } from '@tanstack/react-table'
import type { QuerySpec } from '../lib/querySpec'

/** TanStack sorting → spec.sort; a sort change always goes back to page 1. */
export function specFromSorting(spec: QuerySpec, sorting: SortingState): QuerySpec {
  return { ...spec, page: 1, sort: sorting.map((s) => ({ field: s.id, desc: s.desc })) }
}
