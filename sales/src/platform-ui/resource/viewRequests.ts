import { useSyncExternalStore } from 'react'

// "Open this list with that system view" from elsewhere (e.g. the old
// /review address → Orders, Needs review). A store rather than a query string:
// list pages live in keep-alive tabs, and an open tab must apply the request
// too, not only a freshly mounted one.

const pending = new Map<string, string>()
const listeners = new Set<() => void>()

export function requestView(resource: string, viewKey: string): void {
  pending.set(resource, viewKey)
  listeners.forEach((l) => l())
}

/** Taken once: the list that applies it clears it. */
export function takeViewRequest(resource: string): string | undefined {
  const key = pending.get(resource)
  pending.delete(resource)
  return key
}

export function useViewRequest(resource: string): string | undefined {
  return useSyncExternalStore(
    (l) => { listeners.add(l); return () => listeners.delete(l) },
    () => pending.get(resource),
  )
}
