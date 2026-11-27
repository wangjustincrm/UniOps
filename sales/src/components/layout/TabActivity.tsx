import { useMemo, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { deriveTabMeta, useOptionalTabStoreApi, useTabStore, type RouteDef } from '@uniops/shell'
import { PageActivityContext } from '@/platform-ui'
import { useTabSession } from './tabSession'

const ALWAYS = { active: true }

/** Says to a page (and what it opens) whether its app tab is the active one (user 2026-11-26): the shell keeps up to 15 page tabs
 *  mounted; one that is not shown must not tick. Sales-side, over the shell's store, with no change to the shell: inside a tab the
 *  location is the tab's own path, so its key is derived as the shell derives it. Outside a tab store (embedded): always active. */
export function TabActivity({ routes, children }: { routes: RouteDef[]; children: ReactNode }) {
  return useOptionalTabStoreApi()
    ? <InTab routes={routes}>{children}</InTab>
    : <PageActivityContext.Provider value={ALWAYS}>{children}</PageActivityContext.Provider>
}

function InTab({ routes, children }: { routes: RouteDef[]; children: ReactNode }) {
  const { pathname } = useLocation()
  const key = useMemo(() => deriveTabMeta(routes, pathname)?.key, [routes, pathname])
  const active = useTabStore((s) => !key || s.activeKey === key)
  const refreshTick = useTabSession((s) => (key ? s.ticks[key] ?? 0 : 0))  // its tab's Refresh (the tab bar's menu)
  const value = useMemo(() => ({ active, refreshTick }), [active, refreshTick])
  return <PageActivityContext.Provider value={value}>{children}</PageActivityContext.Provider>
}
