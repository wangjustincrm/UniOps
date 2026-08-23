import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useOptionalTabStoreApi } from './TabStoreContext'
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
  const api = useOptionalTabStoreApi()
  const navigate = useNavigate()
  const { pathname, search } = useLocation()

  return useCallback(
    (toPath: string) => {
      // Split a possible query off the destination so matching uses the pathname.
      const qIdx = toPath.indexOf('?')
      const toPathname = qIdx === -1 ? toPath : toPath.slice(0, qIdx)
      const toSearch = qIdx === -1 ? '' : toPath.slice(qIdx)
      // Outside a tab shell (iframe embed) there is no tab to replace; the
      // navigation itself must still happen.
      if (api) {
        const toMeta = deriveTabMeta(routes, toPathname, toSearch)
        const fromMeta = deriveTabMeta(routes, pathname, search)
        if (toMeta && fromMeta) {
          api.getState().replaceTab(fromMeta.key, toMeta)
        } else if (toMeta) {
          api.getState().openTab(toMeta)
        }
      }
      navigate(toPath)
    },
    [api, navigate, pathname, routes],
  )
}
