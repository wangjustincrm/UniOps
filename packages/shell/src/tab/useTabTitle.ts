import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useTabStoreApi } from './TabStoreContext'
import { deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

/**
 * Rename the tab this page lives in once its real identity is known.
 *
 * Tab titles come from the route table, which is evaluated at navigation time —
 * before the document has been fetched — so an id-keyed route can only fall back
 * to something derived from the URL (a UUID fragment). Detail and edit pages call
 * this with the document number as soon as the query resolves, turning
 * `PO a5f40064` into `PO PO-20260815-0007`.
 *
 * Pass `undefined` while loading (or when the document has no number) to leave the
 * route table's fallback title in place.
 */
export function useTabTitle(routes: RouteDef[], title: string | undefined | null) {
  const api = useTabStoreApi()
  const { pathname } = useLocation()

  useEffect(() => {
    if (!title) return
    const meta = deriveTabMeta(routes, pathname)
    if (!meta) return
    api.getState().updateTitle(meta.key, title)
  }, [api, routes, pathname, title])
}
