import type { RouteDef } from '@uniops/shell'
import SalesForecastPage from '@/pages/forecast/SalesForecastPage'
import ConsignmentStockPage from '@/pages/consignment/ConsignmentStockPage'
import BomExplorerPage from '@/pages/bom/BomExplorerPage'
import CapacityRulesPage from '@/pages/capacity/CapacityRulesPage'
import ProductionPlanPage from '@/pages/mps/ProductionPlanPage'
import SupplyParametersPage from '@/pages/supply/SupplyParametersPage'
import PurchaseSuggestionsPage from '@/pages/purchase/PurchaseSuggestionsPage'

// Real pages land in Tasks 10-12 (forecast grid, consignment entry, BOM tree).
// This route table is the scaffold every later task extends.
export const mrpRoutes: RouteDef[] = [
  { path: '/forecast', element: <SalesForecastPage />, tab: { title: 'Sales Forecast', icon: 'TrendingUp', keyStrategy: 'static', pinned: true } },
  { path: '/consignment-stock', element: <ConsignmentStockPage />, tab: { title: 'Consignment Stock', icon: 'PackageSearch', keyStrategy: 'static' } },
  { path: '/bom-explorer', element: <BomExplorerPage />, tab: { title: 'BOM Explorer', icon: 'Network', keyStrategy: 'static' } },
  { path: '/capacity-rules', element: <CapacityRulesPage />, tab: { title: 'Capacity Rules', icon: 'SlidersHorizontal', keyStrategy: 'static' } },
  { path: '/production-plan', element: <ProductionPlanPage />, tab: { title: 'Production Plan', icon: 'CalendarRange', keyStrategy: 'static' } },
  { path: '/supply-parameters', element: <SupplyParametersPage />, tab: { title: 'Supply Parameters', icon: 'Truck', keyStrategy: 'static' } },
  { path: '/purchase-suggestions', element: <PurchaseSuggestionsPage />, tab: { title: 'Purchase Suggestions', icon: 'ShoppingCart', keyStrategy: 'static' } },
]
