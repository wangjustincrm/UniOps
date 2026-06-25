import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useTabStoreApi } from './TabStoreContext'
import { deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

export function TabRouterSync({ routes }: { routes: RouteDef[] }) {
  const api = useTabStoreApi()
  const { pathname, search } = useLocation()

  useEffect(() => {
    const meta = deriveTabMeta(routes, pathname, search)
    if (!meta) return
    if (api.getState().activeKey === meta.key) {
      // Same tab, but the URL (e.g. its query string) may have changed in place
      // — keep its pinned location in sync so query params reach the page.
      api.getState().setTabPath(meta.key, meta.path!)
      return
    }
    api.getState().openTab(meta) // openTab dedups → focus or create
  }, [pathname, search, routes, api])

  return null
}
