import type { RouteDef } from '@uniops/shell'
import HomePage from '@/pages/HomePage'

// Module routes. Every route also appears in NAV (components/layout/AppLayout)
// with the permission key that gates it.
export const moduleRoutes: RouteDef[] = [
  {
    path: '/home',
    element: <HomePage />,
    tab: { title: 'Home', icon: 'Home', keyStrategy: 'static' },
  },
]
