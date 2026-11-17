import { useSyncExternalStore } from 'react'

// Which tour stops this viewer has seen (the user, 2026-11-17: per stop, not per page, so a tool seen on
// one list isn't "new" on the next): stop id → the version seen. A convenience kept in this browser only;
// unreadable or lost, the tips just play again.

const KEY = 'help-seen-stops'
type Seen = Record<string, number>
let cache: Seen | null = null
const listeners = new Set<() => void>()

function read(): Seen {
  if (cache) return cache
  try { cache = JSON.parse(localStorage.getItem(KEY) ?? '{}') ?? {} } catch { cache = {} }
  return cache!
}
function write(next: Seen) {
  cache = next
  try { localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* private window: kept for this visit only */ }
  listeners.forEach((l) => l())
}

// Another tab of this app changed it (a tour there): read it again here.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key !== KEY && e.key !== null) return
    cache = null
    listeners.forEach((l) => l())
  })
}

export const seenStore = {
  all: read,
  isSeen: (id: string, v = 1) => (read()[id] ?? 0) >= v,
  mark: (stops: { id: string; v?: number }[]) => {
    const next = { ...read() }
    let changed = false
    for (const s of stops) if ((next[s.id] ?? 0) < (s.v ?? 1)) { next[s.id] = s.v ?? 1; changed = true }
    if (changed) write(next)
  },
  /** "Replay all tips": nothing seen. */
  reset: () => write({}),
  subscribe: (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } },
}

/** The seen map, re-rendering when it changes. */
export const useSeen = () => useSyncExternalStore(seenStore.subscribe, seenStore.all, seenStore.all)
