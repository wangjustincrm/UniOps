import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useTabStoreApi } from './TabStoreContext'
import { deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

export function TabRouterSync({ routes }: { routes: RouteDef[] }) {
  const api = useTabStoreApi()
  const { pathname } = useLocation()

  useEffect(() => {
    const meta = deriveTabMeta(routes, pathname)
    if (!meta) return
    if (api.getState().activeKey === meta.key) return // already showing this tab
    api.getState().openTab(meta) // openTab dedups → focus or create
  }, [pathname, routes, api])

  return null
}
