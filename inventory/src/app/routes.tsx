import type { RouteDef } from '@uniops/shell'
import SettingsPage from '@/pages/settings/SettingsPage'
import NcInventoryPage from '@/pages/nc/NcInventoryPage'
import ReconciliationPage from '@/pages/recon/ReconciliationPage'
import TracePage from '@/pages/trace/TracePage'
import WmsInventoryPage from '@/pages/wms/InventoryPage'

// Module navigation (design §7): WMS Inventory / NC Inventory /
// Reconciliation / Trace / Settings. Every page reads under inventory.view
// (AppLayout shows a no-access screen without it).
//
// Trace is one tab PER MATERIAL (/trace/:material, batch and date in the
// query): a single static tab keyed on ?material= would only be focused —
// with its old content — when another page links to a different material.
// /trace on its own is the entry form.
export const inventoryRoutes: RouteDef[] = [
  {
    path: '/wms',
    element: <WmsInventoryPage />,
    tab: { title: 'WMS Inventory', icon: 'Boxes', keyStrategy: 'static' },
  },
  {
    path: '/nc',
    element: <NcInventoryPage />,
    tab: { title: 'NC Inventory', icon: 'Database', keyStrategy: 'static' },
  },
  {
    path: '/reconciliation',
    element: <ReconciliationPage />,
    tab: { title: 'Reconciliation', icon: 'GitCompare', keyStrategy: 'static' },
  },
  {
    path: '/trace',
    element: <TracePage />,
    tab: { title: 'Trace', icon: 'Route', keyStrategy: 'static' },
  },
  {
    path: '/trace/:material',
    element: <TracePage />,
    tab: { title: (p) => `Trace ${p.material}`, icon: 'Route', keyStrategy: 'param', paramName: 'material' },
  },
  {
    path: '/settings',
    element: <SettingsPage />,
    tab: { title: 'Settings', icon: 'Settings', keyStrategy: 'static' },
  },
]
