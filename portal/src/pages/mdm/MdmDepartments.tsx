import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@/store/auth'
import { PortalPageLayout } from '@/components/layout/PortalPageLayout'
import { DepartmentManagement } from '@/pages/admin/AdminPanel'

/** Portal → MDM → Departments. Moved out of the Admin Panel; same access (System Admin). */
export default function MdmDepartments() {
  const { user } = useAuthStore()
  if (user?.role !== 'system_admin') return <Navigate to="/" replace />
  return (
    <PortalPageLayout activeKey="portal:/mdm/departments">
      <DepartmentManagement />
    </PortalPageLayout>
  )
}
