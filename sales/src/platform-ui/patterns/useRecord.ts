import { useQuery, type QueryKey } from '@tanstack/react-query'
import { queryKeys } from '../resource/queryKeys'
import type { Page, QuerySpec } from '../resource/querySpec'

// THE ways a page reads data (2026-11-18): a record, a list inside a page or sheet, a lookup. Pages never call
// useQuery themselves (ESLint), so every read has its key from the resource (a refresh after an action reaches
// it), is disabled until it has what it needs, and works with useRefresh (pass the returned query and its key).

interface Resource { basePath: string }

export interface ReadOptions {
  /** Poll while something runs (ms), else false. */
  refetchInterval?: number | false | ((data: unknown) => number | false)
  /** Off: no fetch (e.g. a tab not shown yet). */
  enabled?: boolean
  /** How long a result counts as fresh (ms): a lookup that rarely changes. */
  staleTime?: number
}

/** One record of a resource, under queryKeys.detail (its list, its sheet and its history refresh together).
 *  No id: nothing is fetched. `fetch` reads it (default `resource.get`). */
export function useRecord<T>(resource: Resource & { get?: (id: number) => Promise<T> }, id: number | null | undefined,
  fetch?: (id: number) => Promise<T>, opts: ReadOptions = {}) {
  const read = fetch ?? resource.get
  return useQuery({
    queryKey: queryKeys.detail(resource, id ?? 'none'),
    queryFn: () => read!(id as number),
    enabled: id != null && !!read && opts.enabled !== false,
    refetchInterval: typeof opts.refetchInterval === 'function'
      ? (q) => (opts.refetchInterval as (d: unknown) => number | false)(q.state.data) : opts.refetchInterval,
    staleTime: opts.staleTime,
  })
}
/** The key useRecord reads under (for useRefresh and setQueryData). */
export const recordKey = (resource: Resource, id: number | null | undefined): QueryKey => queryKeys.detail(resource, id ?? 'none')

/** Rows of a resource inside a page or a sheet (an item's lots, a count's lines), under the resource's list
 *  key + the spec, so an action that refreshes the list refreshes them too. */
export function useRecordList<T>(resource: Resource & { list: (spec: QuerySpec) => Promise<Page<T>> }, spec: QuerySpec | null,
  opts: ReadOptions = {}) {
  return useQuery({
    queryKey: [...queryKeys.list(resource), spec],
    queryFn: () => resource.list(spec!),
    enabled: spec != null && opts.enabled !== false,
    refetchInterval: typeof opts.refetchInterval === 'function'
      ? (q) => (opts.refetchInterval as (d: unknown) => number | false)(q.state.data) : opts.refetchInterval,
    staleTime: opts.staleTime,
  })
}

/** Anything else a page reads (settings, a dashboard, a choice list, a record's own sub-list): under the key given,
 *  which the page shares with whoever refreshes or sets it. No key (null): nothing is fetched. */
export function useLookup<T>(key: QueryKey | null, fetch: () => Promise<T>, opts: ReadOptions = {}) {
  return useQuery({
    queryKey: key ?? ['platform-lookup', 'none'],
    queryFn: fetch,
    enabled: key != null && opts.enabled !== false,
    refetchInterval: typeof opts.refetchInterval === 'function'
      ? (q) => (opts.refetchInterval as (d: unknown) => number | false)(q.state.data) : opts.refetchInterval,
    staleTime: opts.staleTime,
  })
}
