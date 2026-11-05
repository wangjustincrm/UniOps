import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { deriveTabMeta, useOptionalTabStoreApi, useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'

/**
 * Navigate to a static-key tab (e.g. /pa/new, /invoices) with a new query.
 *
 * A static-key tab is chosen by path alone. The tab shell only FOCUSES an
 * already-open tab — it does not overwrite its path — so a link to
 * "/pa/new?source=freight&invoice=X" would land on whatever New PA showed last.
 * Point the open tab at the new path first (same fix as finance
 * CostAnomaliesPage `retarget`). useOptionalTabStoreApi: null when the page is
 * iframe-embedded without a tab shell — the navigation still happens.
 */
export function useStaticTabNav() {
  const tabs = useOptionalTabStoreApi()
  const navigate = useNavigate()
  const replaceTab = useReplaceTab(epmsRoutes)

  const retarget = useCallback((pathname: string, query = '') => {
    const search = query ? `?${query}` : ''
    const path = `${pathname}${search}`
    if (!tabs) return path
    const meta = deriveTabMeta(epmsRoutes, pathname, search)
    if (meta?.path && tabs.getState().tabs.some((t) => t.key === meta.key)) {
      tabs.getState().setTabPath(meta.key, meta.path)
    }
    return path
  }, [tabs])

  /** Open (or focus and re-point) the tab. */
  const openTab = useCallback(
    (pathname: string, query?: string) => navigate(retarget(pathname, query)),
    [navigate, retarget])

  /** Same, but the current tab is closed in favour of it (after a delete). */
  const replaceWithTab = useCallback(
    (pathname: string, query?: string) => replaceTab(retarget(pathname, query)),
    [replaceTab, retarget])

  return { openTab, replaceWithTab }
}
