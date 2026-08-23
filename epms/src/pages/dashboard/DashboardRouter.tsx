import { useAuthStore } from '@/stores/auth.store'
import { useDashboard } from '@/hooks/useDashboard'
import RequesterDashboard from './RequesterDashboard'
import ApproverDashboard from './ApproverDashboard'
import ProcurementDashboard from './ProcurementDashboard'
import WarehouseDashboard from './WarehouseDashboard'
import ApClerkDashboard from './ApClerkDashboard'
import PaymentOfficerDashboard from './PaymentOfficerDashboard'
import FinanceManagerDashboard from './FinanceManagerDashboard'
import FinanceBpDashboard from './FinanceBpDashboard'
import CfoDashboard from './CfoDashboard'
import AuditorDashboard from './AuditorDashboard'
import VendorManagerDashboard from './VendorManagerDashboard'
import SystemAdminDashboard from './SystemAdminDashboard'

/** JWT primary role → dashboard kind. Used only for the first paint, before
 *  GET /dashboard answers; the backend's `role` is authoritative after that.
 *  Keep the two in agreement — the backend's build() is the source of truth. */
function kindFromPrimaryRole(role: string): string {
  switch (role) {
    case 'dept_manager': case 'gm': case 'opm':
    case 'director': case 'supervisor': case 'dept_admin':
      return 'approver'
    case 'procurement_officer': case 'procurement_manager':
      return 'procurement'
    case 'warehouse_staff':
      return 'warehouse'
    default:
      return role
  }
}

export default function DashboardRouter() {
  const { user } = useAuthStore()
  const { data } = useDashboard()

  // The payload's `role` is the DASHBOARD KIND the backend actually built
  // ('approver', 'warehouse', 'payment_officer', …), not a role code. Branching
  // on it instead of on the JWT role means the two can't drift, and it is the
  // only way an ADDITIONAL role (payment_officer is never a primary role) can
  // ever reach its own dashboard. Every component below re-reads the same
  // cached query, so this costs no extra request.
  const kind: string = data?.role ?? kindFromPrimaryRole(user?.role ?? 'requester')

  switch (kind) {
    case 'requester':
      return <RequesterDashboard />
    case 'approver':
      return <ApproverDashboard />
    case 'procurement':
      return <ProcurementDashboard />
    case 'warehouse':
      return <WarehouseDashboard />
    case 'ap_clerk':
      return <ApClerkDashboard />
    case 'payment_officer':
      return <PaymentOfficerDashboard />
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
