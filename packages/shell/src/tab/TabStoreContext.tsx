import { createContext, useContext, useRef, type ReactNode } from 'react'
import { useStore } from 'zustand'
import { createTabStore, type TabStoreOptions, type TabStoreState } from './tabStore'

type TabStoreApi = ReturnType<typeof createTabStore>

const TabStoreContext = createContext<TabStoreApi | null>(null)

export function TabStoreProvider({ options, children }: { options: TabStoreOptions; children: ReactNode }) {
  // Create exactly once per provider lifetime.
  const ref = useRef<TabStoreApi>(null)
  if (ref.current === null) ref.current = createTabStore(options)
  return <TabStoreContext.Provider value={ref.current}>{children}</TabStoreContext.Provider>
}

export function useTabStoreApi(): TabStoreApi {
  const api = useContext(TabStoreContext)
  if (!api) throw new Error('useTabStore must be used within <TabStoreProvider>')
  return api
}

export function useTabStore<T>(selector: (s: TabStoreState) => T): T {
  return useStore(useTabStoreApi(), selector)
}
