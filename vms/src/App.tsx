import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'
import VisitListPage from '@/pages/VisitListPage'
import VisitCreatePage from '@/pages/VisitCreatePage'
import VisitDetailPage from '@/pages/VisitDetailPage'
import ActiveVisitsPage from '@/pages/ActiveVisitsPage'
import BadgePrintPage from '@/pages/BadgePrintPage'
import CheckOutPage from '@/pages/CheckOutPage'
import DashboardPage from '@/pages/DashboardPage'
import AuditLogPage from '@/pages/AuditLogPage'
import ReportsPage from '@/pages/ReportsPage'
import HealthDeclarationsPage from '@/pages/HealthDeclarationsPage'
import TaskInboxPage from '@/pages/TaskInboxPage'
import VisitorCompliancePage from '@/pages/VisitorCompliancePage'
import AdminPanel from '@/pages/admin/AdminPanel'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            {/* Today's visits */}
            <Route index element={<VisitListPage scope="today" />} />

            {/* All visits */}
            <Route path="all" element={<VisitListPage scope="all" />} />

            {/* Currently on-site */}
            <Route path="active" element={<ActiveVisitsPage />} />

            {/* New visit appointment */}
            <Route path="new" element={<VisitCreatePage />} />

            {/* QR-driven check-out (scan or paste a visit UUID) */}
            <Route path="check-out" element={<CheckOutPage />} />

            {/* Dashboard (counters + today’s table) */}
            <Route path="dashboard" element={<DashboardPage />} />

            {/* Task inbox (visit approval tasks for the current user) */}
            <Route path="tasks" element={<TaskInboxPage />} />

            {/* HR / Janitor compliance confirmation for a specific visitor */}
            <Route path="visitor/:visitorId/compliance" element={<VisitorCompliancePage />} />

            {/* Audit log (auditor / system_admin only — also enforced server-side) */}
            <Route path="audit-log" element={<AuditLogPage />} />

            {/* Compliance reports (auditor / system_admin only — server gate) */}
            <Route path="reports" element={<ReportsPage />} />

            {/* Signed health declarations browser (visibility-scoped server-side) */}
            <Route path="health-declarations" element={<HealthDeclarationsPage />} />

            {/* VMS Admin Panel (system_admin only — UI + server gate) */}
            <Route path="admin/*" element={<AdminPanel />} />

            {/* Badge printing (= check-in) — keep BEFORE :visitId so the
                literal segment wins the match. */}
            <Route path="badge/:visitId" element={<BadgePrintPage />} />

            {/* Visit detail — must come after the other static routes */}
            <Route path=":visitId" element={<VisitDetailPage />} />

            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
