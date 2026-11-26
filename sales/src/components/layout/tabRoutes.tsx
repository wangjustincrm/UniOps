import type { RouteDef } from '@uniops/shell'
import { TabActivity } from './TabActivity'

/** The module's routes, each page told whether its app tab is the active one (TabActivity). */
export function withTabActivity(routes: RouteDef[]): RouteDef[] {
  return routes.map((r) => ({ ...r, element: <TabActivity routes={routes}>{r.element}</TabActivity> }))
}
