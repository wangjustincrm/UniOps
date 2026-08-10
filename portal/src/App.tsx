import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'
import PortalHome from '@/pages/PortalHome'
import LoginPage from '@/pages/LoginPage'
import AdminPanel from '@/pages/admin/AdminPanel'
import LogoutPage from '@/pages/LogoutPage'
import DataMaintenance from '@/pages/admin/DataMaintenance'
import ForcePasswordChangePage from '@/pages/ForcePasswordChangePage'
import AccessControl from '@/pages/admin/AccessControl'
import ApprovalRouting from '@/pages/admin/ApprovalRouting'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, user } = useAuthStore()
  if (!isAuthenticated) return <Navigate to="/login" replace />
  // Password rotation gate — swallows every authenticated route (including the
  // module launcher, so no SSO handoff is minted either) until the account
  // clears must_change_password. /logout stays reachable: it is unguarded.
  if (user?.must_change_password) return <ForcePasswordChangePage />
  return <>{children}</>
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuthStore()
  if (isAuthenticated) return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Portal is now the launcher + platform admin only. Finance/Budget
              moved to the standalone Finance app (finance/, port 5177). */}
          <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />
          <Route path="/" element={<ProtectedRoute><PortalHome /></ProtectedRoute>} />
          <Route path="/admin" element={<ProtectedRoute><AdminPanel /></ProtectedRoute>} />
          <Route
            path="/admin/data-maintenance"
            element={<ProtectedRoute><DataMaintenance /></ProtectedRoute>}
          />
          <Route
            path="/admin/access-control"
            element={<ProtectedRoute><AccessControl /></ProtectedRoute>}
          />
          <Route
            path="/admin/approval-routing"
            element={<ProtectedRoute><ApprovalRouting /></ProtectedRoute>}
          />
          {/* Public logout route — clears Portal session, no auth guard */}
          <Route path="/logout" element={<LogoutPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
