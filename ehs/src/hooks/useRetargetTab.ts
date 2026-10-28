/**
 * Point an already-open tab at a link before following it.
 *
 * ★ Review (uniops-8b): a page told what to show by its query (`/hazards?q=…`,
 * `/toolbox-talks?delivery=…`) is a static tab, and when that tab is already
 * open the shell only focuses it — on purpose, it does not overwrite its path.
 * So the second hazard opened from a report showed the first. Calling this
 * before navigating moves the open tab to the new link. Outside the tab shell
 * (an iframe embed) there is no store and the plain navigation is enough.
 */
import { deriveTabMeta, useOptionalTabStoreApi } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'

export function useRetargetTab() {
  const tabs = useOptionalTabStoreApi()
  return (href: string) => {
    const q = href.indexOf('?')
    if (!tabs || q < 0) return
    const meta = deriveTabMeta(ehsRoutes, href.slice(0, q), href.slice(q))
    if (meta && tabs.getState().tabs.some((t) => t.key === meta.key)) {
      tabs.getState().setTabPath(meta.key, meta.path!)
    }
  }
}
