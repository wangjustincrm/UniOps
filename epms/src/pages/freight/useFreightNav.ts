import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { deriveTabMeta, useOptionalTabStoreApi, useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'

/**
 * Navigate to /freight?<query> from another tab.
 *
 * /freight is a static-key tab whose content is chosen by ?tab=. The tab shell
 * only FOCUSES an already-open tab — it does not overwrite its path — so a link
 * from the invoice page to "?tab=create-pa&invoice=X" would land on whatever the
 * Freight tab showed last. Point the open tab at the new path first (same fix as
 * finance CostAnomaliesPage `retarget`). useOptionalTabStoreApi: null when the
 * page is iframe-embedded without a tab shell — the navigation still happens.
 */
export function useFreightNav() {
  const tabs = useOptionalTabStoreApi()
  const navigate = useNavigate()
  const replaceTab = useReplaceTab(epmsRoutes)

  const retarget = useCallback((query: string) => {
    const path = `/freight?${query}`
    if (!tabs) return path
    const meta = deriveTabMeta(epmsRoutes, '/freight', `?${query}`)
    if (meta?.path && tabs.getState().tabs.some((t) => t.key === meta.key)) {
      tabs.getState().setTabPath(meta.key, meta.path)
    }
    return path
  }, [tabs])

  /** Open (or focus and re-point) the Freight tab. */
  const openFreight = useCallback((query: string) => navigate(retarget(query)), [navigate, retarget])

  /** Same, but the current tab is closed in favour of it (after a delete). */
  const replaceWithFreight = useCallback((query: string) => replaceTab(retarget(query)), [replaceTab, retarget])

  return { openFreight, replaceWithFreight }
}
