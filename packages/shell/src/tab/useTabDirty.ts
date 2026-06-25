import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useTabStoreApi } from './TabStoreContext'
import { deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

export function useTabDirty(routes: RouteDef[], dirty: boolean) {
  const api = useTabStoreApi()
  const { pathname } = useLocation()
  useEffect(() => {
    const meta = deriveTabMeta(routes, pathname)
    if (!meta) return
    api.getState().setDirty(meta.key, dirty)
    return () => api.getState().setDirty(meta.key, false)
  }, [api, routes, pathname, dirty])
}
