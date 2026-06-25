import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTabStoreApi } from './TabStoreContext'
import { deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

/**
 * Navigation for "submit" pages (create / edit forms). Replaces the CURRENT tab
 * (the one the calling page lives in) with the destination instead of opening a
 * second tab — so after a successful create the form tab becomes the result page
 * and can't be re-submitted, and cancelling leaves no stale form tab behind.
 *
 * Because each tab's page renders inside `<Routes location={tabPath}>`, the
 * `useLocation()` here resolves to the calling page's own tab path.
 */
export function useReplaceTab(routes: RouteDef[]) {
  const api = useTabStoreApi()
  const navigate = useNavigate()
  const { pathname } = useLocation()

  return useCallback(
    (toPath: string) => {
      const toMeta = deriveTabMeta(routes, toPath)
      const fromMeta = deriveTabMeta(routes, pathname)
      if (toMeta && fromMeta) {
        api.getState().replaceTab(fromMeta.key, toMeta)
      } else if (toMeta) {
        api.getState().openTab(toMeta)
      }
      navigate(toPath)
    },
    [api, navigate, pathname, routes],
  )
}
