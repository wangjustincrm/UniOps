import { useEffect, useRef } from 'react'
import { hashKey, useQueries, useQuery, type QueryKey, type UseQueryResult } from '@tanstack/react-query'
import { queryKeys } from '../resource/queryKeys'
import { errorText } from '../resource/businessError'
import { usePageActive } from './pageActivity'
import type { Page, QuerySpec } from '../resource/querySpec'
import { useNotices } from './notices'

// THE ways a page reads data (2026-11-18): a record, a list inside a page or sheet, a lookup. Pages never call
// useQuery themselves (ESLint), so every read has its key from the resource (a refresh after an action reaches
// it), is disabled until it has what it needs, works with useRefresh (pass the returned query and its key), and
// says a failure the same way: in the page's or sheet's status row, with Retry (or, `errors: 'inline'`, where the
// page shows it itself: QueryState, a RecordSheet's `error`).

interface Resource { basePath: string }

type Interval = number | false | ((data: unknown) => number | false)

export interface ReadOptions {
  /** Poll while something runs (ms), else false. */
  refetchInterval?: Interval
  /** Off: no fetch (e.g. a tab not shown yet). */
  enabled?: boolean
  /** How long a result counts as fresh (ms): a lookup that rarely changes. */
  staleTime?: number
  /** How long an unused result is kept (ms), and how many times a failure is tried again. */
  gcTime?: number
  retry?: number | boolean
  /** Where a failed read is said: the status row with Retry (default), or 'inline' (the page shows it itself). */
  errors?: 'notice' | 'inline'
  /** Business error codes → words. */
  errorMessages?: Record<string, string>
}

// A page's own polling stops while its app tab is not shown (user 2026-11-26); usePageActive is true outside any tab.
const interval = (i: Interval | undefined, active: boolean) =>
  !active ? false : typeof i === 'function' ? (q: { state: { data: unknown } }) => i(q.state.data) : i

/** A failed read in the status row, with Retry; cleared once it reads again. */
function useReadErrors(q: UseQueryResult<unknown>, key: QueryKey, opts: ReadOptions) {
  const notices = useNotices()
  const id = `read:${hashKey(key)}`
  const shown = useRef(false)
  const failed = opts.errors !== 'inline' && q.isError
  useEffect(() => {
    if (failed) {
      shown.current = true
      notices.notify({ key: id, tone: 'error', text: `Could not load: ${errorText(q.error, opts.errorMessages)}`,
        action: { label: 'Retry', onClick: () => { void q.refetch() } } })
    } else if (shown.current && q.isSuccess) {
      shown.current = false
      notices.clear(id)
    }
  }, [failed, q.isSuccess, q.errorUpdatedAt])  // eslint-disable-line react-hooks/exhaustive-deps
}

/** One record of a resource, under queryKeys.detail (its list, its sheet and its history refresh together).
 *  No id: nothing is fetched. `fetch` reads it (default `resource.get`). */
export function useRecord<T>(resource: Resource, id: number | null | undefined, fetch: (id: number) => Promise<T>,
  opts?: ReadOptions): UseQueryResult<T>
export function useRecord<T>(resource: Resource & { get: (id: number) => Promise<T> }, id: number | null | undefined,
  fetch?: undefined, opts?: ReadOptions): UseQueryResult<T>
export function useRecord<T>(resource: Resource & { get?: (id: number) => Promise<T> }, id: number | null | undefined,
  fetch?: (id: number) => Promise<T>, opts: ReadOptions = {}): UseQueryResult<T> {
  const read = fetch ?? resource.get
  if (!read && import.meta.env.DEV) console.error(`useRecord(${resource.basePath}): no fetch and no get — it would never load`)
  const queryKey = queryKeys.detail(resource, id ?? 'none')
  const active = usePageActive()
  const q = useQuery({
    queryKey,
    queryFn: () => read!(id as number),
    enabled: id != null && !!read && opts.enabled !== false,
    refetchInterval: interval(opts.refetchInterval, active),
    staleTime: opts.staleTime,
  })
  useReadErrors(q, queryKey, opts)
  return q
}
/** The key useRecord reads under (for useRefresh and setQueryData). */
export const recordKey = (resource: Resource, id: number | null | undefined): QueryKey => queryKeys.detail(resource, id ?? 'none')

/** Rows of a resource inside a page or a sheet (an item's lots, a count's lines), under the resource's list
 *  key + the spec, so an action that refreshes the list refreshes them too. No spec: nothing is fetched. */
export function useRecordList<T>(resource: Resource & { list: (spec: QuerySpec) => Promise<Page<T>> }, spec: QuerySpec | null,
  opts: ReadOptions = {}) {
  const queryKey = [...queryKeys.list(resource), spec]
  const active = usePageActive()
  const q = useQuery({
    queryKey,
    queryFn: () => resource.list(spec!),
    enabled: spec != null && opts.enabled !== false,
    refetchInterval: interval(opts.refetchInterval, active),
    staleTime: opts.staleTime,
  })
  useReadErrors(q, queryKey, opts)
  return q
}

/** Anything else a page reads (settings, a dashboard, a choice list, a record's own sub-list): under the key given,
 *  which the page shares with whoever refreshes or sets it. No key (null): nothing is fetched. */
export function useLookup<T>(key: QueryKey | null, fetch: () => Promise<T>, opts: ReadOptions = {}) {
  const queryKey = key ?? ['platform-lookup', 'none']
  const active = usePageActive()
  const q = useQuery({
    queryKey,
    queryFn: fetch,
    enabled: key != null && opts.enabled !== false,
    refetchInterval: interval(opts.refetchInterval, active),
    staleTime: opts.staleTime,
    ...(opts.gcTime !== undefined ? { gcTime: opts.gcTime } : {}),
    ...(opts.retry !== undefined ? { retry: opts.retry } : {}),
  })
  useReadErrors(q, queryKey, opts)
  return q
}

/** The same lookup for several keys at once (e.g. each item's units): the results in the keys' order. */
export function useLookups<T>(keys: QueryKey[], fetch: (key: QueryKey) => Promise<T>, opts: Pick<ReadOptions, 'staleTime'> = {}) {
  return useQueries({ queries: keys.map((queryKey) => ({ queryKey, queryFn: () => fetch(queryKey), staleTime: opts.staleTime })) })
}
