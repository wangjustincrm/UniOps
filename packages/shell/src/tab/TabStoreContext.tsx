import { createContext, useContext, useRef, type ReactNode } from 'react'
import { useStore } from 'zustand'
import { createTabStore, type TabStoreOptions, type TabStoreState } from './tabStore'

type TabStoreApi = ReturnType<typeof createTabStore>

const TabStoreContext = createContext<TabStoreApi | null>(null)

export function TabStoreProvider({ options, children }: { options: TabStoreOptions; children: ReactNode }) {
  // Create once per provider lifetime, and again when the user actually changes.
  //
  // Tabs live only in memory, so unmounting the provider already discards them;
  // this covers the one case where the provider stays mounted across a change of
  // user (an SPA logout → login with no page load), which would otherwise hand
  // the new user the previous user's open tabs.
  //
  // Only transitions between two KNOWN users count. `userId` is read from an auth
  // store that can briefly report `undefined` (rehydrate, token refresh) while a
  // session is perfectly alive, and treating that blip as a user switch would
  // wipe a working set of tabs out from under someone.
  const ref = useRef<{ api: TabStoreApi; userId: string | undefined }>(null)
  if (ref.current === null) {
    ref.current = { api: createTabStore(options), userId: options.userId }
  } else if (
    options.userId !== undefined &&
    ref.current.userId !== undefined &&
    options.userId !== ref.current.userId
  ) {
    ref.current = { api: createTabStore(options), userId: options.userId }
  } else if (options.userId !== undefined && ref.current.userId === undefined) {
    // First time we learn who this is — adopt it without discarding the store.
    ref.current = { api: ref.current.api, userId: options.userId }
  }
  return <TabStoreContext.Provider value={ref.current.api}>{children}</TabStoreContext.Provider>
}

export function useTabStoreApi(): TabStoreApi {
  const api = useContext(TabStoreContext)
  if (!api) throw new Error('useTabStore must be used within <TabStoreProvider>')
  return api
}

/**
 * The tab store when there is one, or null.
 *
 * Pages are not always inside a tab shell: EPMS and OA render their routes with
 * no TabStoreProvider when embedded in an iframe (Finance's AP list drills into
 * an invoice that way, and the embedded app hides its own tab bar on purpose).
 * Page-level hooks must use THIS and degrade, not the throwing accessor above —
 * throwing takes down the whole embedded page. Reserve `useTabStoreApi` for
 * chrome that genuinely cannot exist without a shell, like TabBar and TabHost.
 */
export function useOptionalTabStoreApi(): TabStoreApi | null {
  return useContext(TabStoreContext)
}

export function useTabStore<T>(selector: (s: TabStoreState) => T): T {
  return useStore(useTabStoreApi(), selector)
}
