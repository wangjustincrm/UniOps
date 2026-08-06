import type { RouteDef } from '@uniops/shell'
import ForecastPage from '@/pages/forecast/ForecastPage'
import ConsignmentStockPage from '@/pages/consignment/ConsignmentStockPage'
import BomExplorerPage from '@/pages/bom/BomExplorerPage'
import CapacityRulesPage from '@/pages/capacity/CapacityRulesPage'
import ProductionPlanPage from '@/pages/mps/ProductionPlanPage'

// Real pages land in Tasks 10-12 (forecast grid, consignment entry, BOM tree).
// This route table is the scaffold every later task extends.
export const mrpRoutes: RouteDef[] = [
  { path: '/forecast', element: <ForecastPage />, tab: { title: 'Sales Forecast', icon: 'TrendingUp', keyStrategy: 'static', pinned: true } },
  { path: '/consignment-stock', element: <ConsignmentStockPage />, tab: { title: 'Consignment Stock', icon: 'PackageSearch', keyStrategy: 'static' } },
  { path: '/bom-explorer', element: <BomExplorerPage />, tab: { title: 'BOM Explorer', icon: 'Network', keyStrategy: 'static' } },
  { path: '/capacity-rules', element: <CapacityRulesPage />, tab: { title: 'Capacity Rules', icon: 'SlidersHorizontal', keyStrategy: 'static' } },
  { path: '/production-plan', element: <ProductionPlanPage />, tab: { title: 'Production Plan', icon: 'CalendarRange', keyStrategy: 'static' } },
]
