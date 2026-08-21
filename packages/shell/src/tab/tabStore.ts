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
  /** Update a tab's `path` (e.g. when its query string changes) without
   *  re-opening it — keeps the active tab's pinned location in sync with the URL. */
  setTabPath: (key: string, path: string) => void
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
  /**
   * Maximum number of open tabs before LRU eviction kicks in. Only counts
   * and evicts tabs with `kind: 'page'` — iframe tabs are exempt from the cap.
   */
  cap?: number
  initialTabs?: TabMeta[]
  /**
   * Identity of the current user. The STORE does not read this — tabs are never
   * persisted, so there is nothing to invalidate here. `TabStoreProvider` uses it
   * as the store's identity: when it changes, the provider builds a new store, so
   * a user switch inside a single SPA session never inherits the previous user's
   * tabs.
   */
  userId?: string
}

function touchLru(lru: string[], key: string): string[] {
  return [...lru.filter((k) => k !== key), key]
}

/**
 * The tab workspace is deliberately IN-MEMORY ONLY. Leaving a module (Back to
 * Portal is a full-page navigation), reloading, or reopening the browser all
 * unload the SPA and therefore this store, so the next entry starts with just
 * the pinned initial tabs. Do not re-add a `persist` middleware here: "come back
 * to a clean workspace" is the required behaviour, not an accident.
 */
export function createTabStore(opts: TabStoreOptions) {
  const initial = opts.initialTabs ?? []
  const firstKey = initial[0]?.key ?? ''

  return createStore<TabStoreState>()((set, get) => ({
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

    setTabPath: (key, path) =>
      set((s) => ({ tabs: s.tabs.map((t) => (t.key === key && t.path !== path ? { ...t, path } : t)) })),

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
  }))
}
