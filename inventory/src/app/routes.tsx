import type { RouteDef } from '@uniops/shell'
import PlaceholderPage from '@/pages/placeholder/PlaceholderPage'
import SettingsPage from '@/pages/settings/SettingsPage'
import NcInventoryPage from '@/pages/nc/NcInventoryPage'

// Module navigation (design §7): WMS Inventory / NC Inventory /
// Reconciliation / Trace / Settings. Phase 1 shipped Settings, phase 2 NC
// Inventory; the other three are placeholders until their phase lands. Every page reads under
// inventory.view (AppLayout shows a no-access screen without it).
export const inventoryRoutes: RouteDef[] = [
  {
    path: '/wms',
    element: <PlaceholderPage title="WMS Inventory" message="WMS Inventory moves here from MRP in phase 3." />,
    tab: { title: 'WMS Inventory', icon: 'Boxes', keyStrategy: 'static' },
  },
  {
    path: '/nc',
    element: <NcInventoryPage />,
    tab: { title: 'NC Inventory', icon: 'Database', keyStrategy: 'static' },
  },
  {
    path: '/reconciliation',
    element: <PlaceholderPage title="Reconciliation" message="Coming in phase 4." />,
    tab: { title: 'Reconciliation', icon: 'GitCompare', keyStrategy: 'static' },
  },
  {
    path: '/trace',
    element: <PlaceholderPage title="Trace" message="Coming in phase 5." />,
    tab: { title: 'Trace', icon: 'Route', keyStrategy: 'static' },
  },
  {
    path: '/settings',
    element: <SettingsPage />,
    tab: { title: 'Settings', icon: 'Settings', keyStrategy: 'static' },
  },
]
