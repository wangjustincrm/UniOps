import { useSyncExternalStore } from 'react'

// One clock for every countdown on the page: a table of fifty rows runs one timer, not fifty.
const TICK_MS = 30_000
const listeners = new Set<() => void>()
let now = Date.now()
let timer: ReturnType<typeof setInterval> | null = null

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (timer === null) {
    now = Date.now()
    timer = setInterval(() => { now = Date.now(); listeners.forEach((l) => l()) }, TICK_MS)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && timer !== null) { clearInterval(timer); timer = null }
  }
}

/** The time now (ms since epoch), refreshed every half minute while something shows it. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, () => now)
}
