import type { RouteDef } from '@uniops/shell'
import HomePage from '@/pages/HomePage'
import PartnersPage from '@/features/configuration/partners/PartnersPage'
import ConnectionsPage from '@/features/configuration/connections/ConnectionsPage'
import DocumentsPage from '@/features/operations/DocumentsPage'
import RunsPage from '@/features/operations/RunsPage'

// Module routes. Every route also appears in NAV (components/layout/AppLayout)
// with the permission key that gates it.
export const moduleRoutes: RouteDef[] = [
  {
    path: '/home',
    element: <HomePage />,
    tab: { title: 'Home', icon: 'Home', keyStrategy: 'static' },
  },
  {
    path: '/config/partners',
    element: <PartnersPage />,
    tab: { title: 'Partners', icon: 'Building2', keyStrategy: 'static' },
  },
  {
    path: '/config/connections',
    element: <ConnectionsPage />,
    tab: { title: 'Connections', icon: 'Plug', keyStrategy: 'static' },
  },
  {
    path: '/documents',
    element: <DocumentsPage />,
    tab: { title: 'Documents', icon: 'FileText', keyStrategy: 'static' },
  },
  {
    path: '/runs',
    element: <RunsPage />,
    tab: { title: 'Runs and log', icon: 'Activity', keyStrategy: 'static' },
  },
]
