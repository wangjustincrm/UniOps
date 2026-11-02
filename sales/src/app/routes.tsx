import type { RouteDef } from '@uniops/shell'
import HomePage from '@/pages/HomePage'
import PartnersPage from '@/features/configuration/partners/PartnersPage'
import ConnectionsPage from '@/features/configuration/connections/ConnectionsPage'
import DocumentsPage from '@/features/operations/DocumentsPage'
import RunsPage from '@/features/operations/RunsPage'
import ShadowComparePage from '@/features/shadow/ShadowComparePage'
import DeliveryPage from '@/features/invoices/DeliveryPage'
import InvoicesPage from '@/features/invoices/InvoicesPage'
import OrdersPage from '@/features/orders/OrdersPage'
import ManualOrderPage from '@/features/orders/ManualOrderPage'
import ItemsPage from '@/features/configuration/items/ItemsPage'
import PartnerCodesPage from '@/features/configuration/partner-codes/PartnerCodesPage'
import DcRoutingPage from '@/features/configuration/dc-routing/DcRoutingPage'

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
  { path: '/orders', element: <OrdersPage />, tab: { title: 'Orders', icon: 'ShoppingCart', keyStrategy: 'static' } },
  { path: '/orders/manual', element: <ManualOrderPage />, tab: { title: 'Manual orders', icon: 'PenLine', keyStrategy: 'static' } },
  { path: '/config/items', element: <ItemsPage />, tab: { title: 'Items', icon: 'Package', keyStrategy: 'static' } },
  { path: '/config/partner-codes', element: <PartnerCodesPage />, tab: { title: 'Partner codes', icon: 'Tags', keyStrategy: 'static' } },
  { path: '/config/dc-routing', element: <DcRoutingPage />, tab: { title: 'DC routing', icon: 'Route', keyStrategy: 'static' } },
  {
    path: '/runs',
    element: <RunsPage />,
    tab: { title: 'Runs and log', icon: 'Activity', keyStrategy: 'static' },
  },
  { path: '/shadow', element: <ShadowComparePage />, tab: { title: 'Shadow compare', icon: 'GitCompare', keyStrategy: 'static' } },
  { path: '/deliveries', element: <DeliveryPage />, tab: { title: 'Delivery confirmation', icon: 'Truck', keyStrategy: 'static' } },
  { path: '/invoices', element: <InvoicesPage />, tab: { title: 'Invoices', icon: 'Receipt', keyStrategy: 'static' } },
]
