import { createStore } from 'zustand/vanilla'
import type { TabMeta } from './types'

export interface TabStoreState {
  tabs: TabMeta[]
  activeKey: string
  /** Keys mounted at least once this session (keep-alive set). */
  alive: string[]
  /** Activation order, oldest first — drives LRU eviction. */
  lru: string[]
  openTab: (meta: TabMeta) => void
  closeTab: (key: string) => void
  closeOthers: (key: string) => void
  closeAll: () => void
  setActive: (key: string) => void
  updateTitle: (key: string, title: string) => void
  setDirty: (key: string, dirty: boolean) => void
}

export interface TabStoreOptions {
  storageKey: string
  cap?: number
  initialTabs?: TabMeta[]
}

function touchLru(lru: string[], key: string): string[] {
  return [...lru.filter((k) => k !== key), key]
}

export function createTabStore(opts: TabStoreOptions) {
  const initial = opts.initialTabs ?? []
  const firstKey = initial[0]?.key ?? ''

  return createStore<TabStoreState>((set, get) => ({
    tabs: initial,
    activeKey: firstKey,
    alive: firstKey ? [firstKey] : [],
    lru: firstKey ? [firstKey] : [],

    openTab: (meta) => {
      const cap = opts.cap ?? 15
      const { tabs } = get()
      if (tabs.some((t) => t.key === meta.key)) {
        get().setActive(meta.key)
        return
      }
      set((s) => {
        let nextTabs = [...s.tabs, meta]
        let nextAlive = s.alive.includes(meta.key) ? s.alive : [...s.alive, meta.key]
        const nextLru = touchLru(s.lru, meta.key)

        // Evict LRU non-pinned, non-dirty, non-incoming tabs until within cap.
        while (nextTabs.filter((t) => t.kind === 'page').length > cap) {
          const victimKey = nextLru.find((k) => {
            const t = nextTabs.find((x) => x.key === k)
            return t && !t.pinned && !t.dirty && t.key !== meta.key
          })
          if (!victimKey) break // nothing safe to evict
          nextTabs = nextTabs.filter((t) => t.key !== victimKey)
          nextAlive = nextAlive.filter((k) => k !== victimKey)
        }

        return {
          tabs: nextTabs,
          activeKey: meta.key,
          alive: nextAlive,
          lru: nextLru.filter((k) => nextTabs.some((t) => t.key === k)),
        }
      })
    },

    setActive: (key) => {
      set((s) => {
        if (!s.tabs.some((t) => t.key === key)) return s
        return {
          activeKey: key,
          alive: s.alive.includes(key) ? s.alive : [...s.alive, key],
          lru: touchLru(s.lru, key),
        }
      })
    },

    closeTab: (key) => {
      const { tabs, activeKey } = get()
      const target = tabs.find((t) => t.key === key)
      if (!target || target.pinned) return
      const idx = tabs.findIndex((t) => t.key === key)
      const nextTabs = tabs.filter((t) => t.key !== key)
      let nextActive = activeKey
      if (activeKey === key) {
        const neighbour = nextTabs[idx] ?? nextTabs[idx - 1] ?? nextTabs[nextTabs.length - 1]
        nextActive = neighbour?.key ?? ''
      }
      set((s) => ({
        tabs: nextTabs,
        activeKey: nextActive,
        alive: s.alive.filter((k) => k !== key),
        lru: s.lru.filter((k) => k !== key),
      }))
    },

    closeOthers: (key) => {
      set((s) => {
        const kept = s.tabs.filter((t) => t.pinned || t.key === key)
        const keptKeys = new Set(kept.map((t) => t.key))
        return {
          tabs: kept,
          activeKey: keptKeys.has(s.activeKey) ? s.activeKey : key,
          alive: s.alive.filter((k) => keptKeys.has(k)),
          lru: s.lru.filter((k) => keptKeys.has(k)),
        }
      })
    },

    closeAll: () => {
      set((s) => {
        const kept = s.tabs.filter((t) => t.pinned)
        const keptKeys = new Set(kept.map((t) => t.key))
        const active = kept[0]?.key ?? ''
        return {
          tabs: kept,
          activeKey: active,
          alive: s.alive.filter((k) => keptKeys.has(k)),
          lru: s.lru.filter((k) => keptKeys.has(k)),
        }
      })
    },

    updateTitle: (key, title) =>
      set((s) => ({ tabs: s.tabs.map((t) => (t.key === key ? { ...t, title } : t)) })),

    setDirty: (key, dirty) =>
      set((s) => ({ tabs: s.tabs.map((t) => (t.key === key ? { ...t, dirty } : t)) })),
  }))
}
