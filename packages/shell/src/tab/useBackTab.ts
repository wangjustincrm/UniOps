import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useOptionalTabStoreApi } from './TabStoreContext'
import { deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

/**
 * Navigation for "back to the parent" links (`← Back to PO List`, `Back to PR`).
 *
 * Going back means the current tab has served its purpose, so this CLOSES it and
 * lands on the destination: if the destination tab is already open (the usual
 * case — you reached the detail page from its list) it is focused and the current
 * tab disappears; if it is not open, the current tab becomes the destination in
 * place. Either way the user never accumulates a tab they explicitly navigated
 * away from.
 *
 * Pinned tabs cannot be closed, so from a pinned tab this degrades to plain
 * "open/focus the destination".
 */
export function useBackTab(routes: RouteDef[]) {
  const api = useOptionalTabStoreApi()
  const navigate = useNavigate()
  const { pathname, search } = useLocation()

  return useCallback(
    (toPath: string) => {
      const qIdx = toPath.indexOf('?')
      const toPathname = qIdx === -1 ? toPath : toPath.slice(0, qIdx)
      const toSearch = qIdx === -1 ? '' : toPath.slice(qIdx)
      // Outside a tab shell (iframe embed) there is no tab to close — the link
      // still has to take you to the destination, so only the store work is
      // skipped, never the navigation.
      if (api) {
        const toMeta = deriveTabMeta(routes, toPathname, toSearch)
        const fromMeta = deriveTabMeta(routes, pathname, search)
        if (toMeta && fromMeta && toMeta.key !== fromMeta.key) {
          api.getState().replaceTab(fromMeta.key, toMeta)
        } else if (toMeta) {
          api.getState().openTab(toMeta)
        }
      }
      navigate(toPath)
    },
    [api, navigate, pathname, search, routes],
  )
}
