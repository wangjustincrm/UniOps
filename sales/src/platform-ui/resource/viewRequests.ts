import { useSyncExternalStore } from 'react'
import type { ViewState } from './viewState'

// "Open this list with that system view" from elsewhere (e.g. the old
// /review address → Orders, Needs review) — or with a view made on the spot
// (a dashboard card: its own filters, under its own name); and "open this
// record" (a Task Inbox link). Stores rather than a query string: list pages
// live in keep-alive tabs, and an open tab must apply the request too, not
// only a freshly mounted one.

/** A view made on the spot: shown under `name`, never saved unless the user saves it. */
export interface AdHocView {
  name: string
  state: Partial<ViewState>
}
export type ViewRequest = string | AdHocView

const pending = new Map<string, ViewRequest>()
const listeners = new Set<() => void>()
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }
const notify = () => listeners.forEach((l) => l())

export function requestView(resource: string, view: ViewRequest): void {
  pending.set(resource, view)
  notify()
}

/** Taken once: the list that applies it clears it. */
export function takeViewRequest(resource: string): ViewRequest | undefined {
  const key = pending.get(resource)
  pending.delete(resource)
  return key
}

export function useViewRequest(resource: string): ViewRequest | undefined {
  return useSyncExternalStore(subscribe, () => pending.get(resource))
}

// ── open a record ────────────────────────────────────────────────────────────
const opening = new Map<string, number>()

/** Ask the list at `basePath` (e.g. "/orders") to open record `id` in its sheet. */
export function requestOpen(basePath: string, id: number): void {
  opening.set(basePath, id)
  notify()
}

export function takeOpenRequest(basePath: string): number | undefined {
  const id = opening.get(basePath)
  opening.delete(basePath)
  return id
}

export function useOpenRequest(basePath: string): number | undefined {
  return useSyncExternalStore(subscribe, () => opening.get(basePath))
}
