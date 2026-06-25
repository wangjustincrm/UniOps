import { createStore } from 'zustand/vanilla'
import { persist, createJSONStorage } from 'zustand/middleware'
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
  /**
   * Replace the tab `oldKey` with `meta`, keeping its position and activating
   * `meta`. For "submit" pages (create/edit) so submitting navigates in place
   * instead of spawning a second tab. Falls back to opening `meta` when `oldKey`
   * is pinned or missing; if `meta` is already open elsewhere, the old tab is
   * dropped and the existing one focused.
   */
  replaceTab: (oldKey: string, meta: TabMeta) => void
}

export interface TabStoreOptions {
  storageKey: string
  /**
   * Maximum number of open tabs before LRU eviction kicks in. Only counts
   * and evicts tabs with `kind: 'page'` — iframe tabs are exempt from the cap.
   */
  cap?: number
  initialTabs?: TabMeta[]
  /**
   * Identity of the current user. Persisted alongside the tabs; on rehydrate, if
   * it differs from the stored value the persisted tabs are discarded and the
   * store starts fresh (initialTabs only). This closes the previous user's tabs
   * after a user switch. Omit to disable the check (tabs always restore).
   */
  userId?: string
}

function touchLru(lru: string[], key: string): string[] {
  return [...lru.filter((k) => k !== key), key]
}

export function createTabStore(opts: TabStoreOptions) {
  const initial = opts.initialTabs ?? []
  const firstKey = initial[0]?.key ?? ''

  return createStore<TabStoreState>()(
    persist(
      (set, get) => ({
    tabs: initial,
    activeKey: firstKey,
    alive: firstKey ? [firstKey] : [],
    lru: firstKey ? [firstKey] : [],

    openTab: (meta) => {
      const cap = opts.cap ?? 15
      const { tabs } = get()
      if (tabs.some((t) => t.key === meta.key)) {
        // Re-opening an existing tab focuses it; it intentionally does NOT
        // overwrite existing metadata (use updateTitle to change a title).
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

    replaceTab: (oldKey, meta) => {
      const old = get().tabs.find((t) => t.key === oldKey)
      // Can't replace a pinned or missing tab — just open/focus the target.
      if (!old || old.pinned) {
        get().openTab(meta)
        return
      }
      set((s) => {
        const destExists = s.tabs.some((t) => t.key === meta.key) && meta.key !== oldKey
        const nextTabs = destExists
          ? s.tabs.filter((t) => t.key !== oldKey) // destination already open → drop old, focus it
          : s.tabs.map((t) => (t.key === oldKey ? meta : t)) // replace in place
        return {
          tabs: nextTabs,
          activeKey: meta.key,
          alive: [
            ...s.alive.filter((k) => k !== oldKey),
            ...(s.alive.includes(meta.key) ? [] : [meta.key]),
          ],
          lru: touchLru(s.lru.filter((k) => k !== oldKey), meta.key),
        }
      })
    },
      }),
      {
        name: opts.storageKey,
        storage: createJSONStorage(() => localStorage),
        partialize: (s) => ({ tabs: s.tabs, activeKey: s.activeKey, userId: opts.userId }),
        version: 1,
        merge: (persisted, current) => {
          const p = (persisted ?? {}) as Partial<TabStoreState> & { userId?: string }
          // User switched (persisted tabs belong to a different user) → discard
          // them and start fresh with just the initial (pinned) tabs.
          if (opts.userId !== undefined && p.userId !== opts.userId) {
            return current
          }
          let tabs = p.tabs ?? current.tabs
          // Guarantee pinned initial tabs exist, sit first, and carry their
          // canonical identity flags — even if a persisted tab with the same
          // key has stale flags (e.g. pinned:false) from before it was
          // configured as a pinned initial tab.
          for (const pin of initial) {
            const idx = tabs.findIndex((t) => t.key === pin.key)
            if (idx === -1) {
              tabs = [pin, ...tabs]
            } else {
              const existing = tabs[idx]
              const repaired = { ...existing, pinned: pin.pinned, closable: pin.closable, icon: pin.icon, title: pin.title }
              tabs = [...tabs.slice(0, idx), repaired, ...tabs.slice(idx + 1)]
            }
          }
          const activeKey =
            p.activeKey && tabs.some((t) => t.key === p.activeKey)
              ? p.activeKey
              : tabs[0]?.key ?? ''
          return { ...current, tabs, activeKey, alive: activeKey ? [activeKey] : [], lru: activeKey ? [activeKey] : [] }
        },
      },
    ),
  )
}
