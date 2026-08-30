/**
 * Route registration — the single source for both the router and the tab shell.
 */
import type { RouteDef } from '@uniops/shell'
import IncidentListPage from '@/pages/IncidentListPage'
import MyActionsPage from '@/pages/MyActionsPage'

export const ehsRoutes: RouteDef[] = [
  {
    path: '/actions/mine',
    element: <MyActionsPage />,
    tab: { title: 'My actions', icon: 'ClipboardCheck', keyStrategy: 'static', pinned: true },
  },
  {
    path: '/incidents',
    element: <IncidentListPage />,
    tab: { title: 'Incidents', icon: 'TriangleAlert', keyStrategy: 'static' },
  },
]
