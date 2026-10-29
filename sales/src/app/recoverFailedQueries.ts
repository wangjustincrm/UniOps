import { useEffect, useRef } from 'react'
import { useQueryClient, type Query, type QueryClient } from '@tanstack/react-query'

// Pages live in keep-alive tabs: switching tabs neither remounts a page nor
// refetches its queries, and window-focus refetching is off (it would reload
// every list whenever the user comes back to the browser). So a list whose
// request failed once — e.g. while sales-api was restarting — stayed on
// "Failed to fetch" with no new request, until "Try again" (2026-10-29: the
// Partners page on dev2). A failed query now retries when its tab becomes
// active again and when the window regains focus; healthy ones do not.

export const failed = (q: Query) => q.state.status === 'error'

/** QueryClient default: refetch on window focus only what failed. */
export const refetchOnWindowFocus = (q: Query) => failed(q)

export function refetchFailed(qc: QueryClient) {
  return qc.refetchQueries({ predicate: failed, type: 'active' })
}

/** Refetches failed queries whenever the active tab changes (not on mount). */
export function useRecoverFailedQueries(activeKey: string | undefined) {
  const qc = useQueryClient()
  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    void refetchFailed(qc)
  }, [activeKey, qc])
}
