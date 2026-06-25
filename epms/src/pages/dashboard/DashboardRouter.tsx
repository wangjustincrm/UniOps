import { useAuthStore } from '@/stores/auth.store'
import RequesterDashboard from './RequesterDashboard'
import ApproverDashboard from './ApproverDashboard'
import ProcurementDashboard from './ProcurementDashboard'
import WarehouseDashboard from './WarehouseDashboard'
import ApClerkDashboard from './ApClerkDashboard'
import FinanceManagerDashboard from './FinanceManagerDashboard'
import FinanceBpDashboard from './FinanceBpDashboard'
import CfoDashboard from './CfoDashboard'
import AuditorDashboard from './AuditorDashboard'
import VendorManagerDashboard from './VendorManagerDashboard'
import SystemAdminDashboard from './SystemAdminDashboard'

export default function DashboardRouter() {
  const { user } = useAuthStore()
  const role = user?.role ?? 'requester'

  switch (role) {
    case 'requester':
      return <RequesterDashboard />
    case 'dept_manager':
    case 'gm':
    case 'opm':
      return <ApproverDashboard />
    case 'procurement_officer':
    case 'procurement_manager':
      return <ProcurementDashboard />
    case 'warehouse_staff':
      return <WarehouseDashboard />
    case 'ap_clerk':
      return <ApClerkDashboard />
    case 'finance_manager':
      return <FinanceManagerDashboard />
    case 'finance_bp':
      return <FinanceBpDashboard />
    case 'cfo':
      return <CfoDashboard />
    case 'auditor':
      return <AuditorDashboard />
    case 'vendor_manager':
      return <VendorManagerDashboard />
    case 'system_admin':
      return <SystemAdminDashboard />
    default:
      return <RequesterDashboard />
  }
}
