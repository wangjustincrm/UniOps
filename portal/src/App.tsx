import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'
import { encodeSession, safeReturnUrl, goToReturnUrl } from '@/lib/api'
import PortalHome from '@/pages/PortalHome'
import LoginPage from '@/pages/LoginPage'
import AdminPanel from '@/pages/admin/AdminPanel'
import LogoutPage from '@/pages/LogoutPage'
import DataMaintenance from '@/pages/admin/DataMaintenance'
import ForcePasswordChangePage from '@/pages/ForcePasswordChangePage'
import AccessControl from '@/pages/admin/AccessControl'
import ApprovalRouting from '@/pages/admin/ApprovalRouting'
import ApprovalDelegation from '@/pages/admin/ApprovalDelegation'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, user } = useAuthStore()
  const { search } = useLocation()
  // Carry the query string across (a sub-app's ?returnUrl=… arrives on "/"):
  // a bare Navigate would drop it and the user would lose the page they asked
  // for after signing in.
  if (!isAuthenticated) return <Navigate to={{ pathname: '/login', search }} replace />
  // Password rotation gate — swallows every authenticated route (including the
  // module launcher, so no SSO handoff is minted either) until the account
  // clears must_change_password. /logout stays reachable: it is unguarded.
  if (user?.must_change_password) return <ForcePasswordChangePage />
  return <>{children}</>
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuthStore()
  const { search } = useLocation()
  if (isAuthenticated) return <Navigate to={{ pathname: '/', search }} replace />
  return <>{children}</>
}

/**
 * Bounce an already-signed-in user straight back to the module that sent them.
 *
 * A sub-app whose own session lapsed redirects to `${PORTAL_URL}?returnUrl=…`.
 * When Portal's session is still good there is no login form to go through, so
 * the launcher would just sit there and the user would have to click the module
 * again. Mint the handoff here instead. Sits inside ProtectedRoute, so the
 * password-rotation gate still wins over it.
 */
function ReturnUrlGate({ children }: { children: React.ReactNode }) {
  const { user, token, refreshToken } = useAuthStore()
  const { search } = useLocation()
  const target = safeReturnUrl(new URLSearchParams(search).get('returnUrl'))
  const leaving = !!(target && user && token)

  useEffect(() => {
    if (target && user && token) {
      goToReturnUrl(target, encodeSession(token, refreshToken ?? '', user))
    }
  }, [target, user, token, refreshToken])

  if (leaving) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-neutral-500">
        Signing you in…
      </div>
    )
  }
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
          <Route path="/" element={<ProtectedRoute><ReturnUrlGate><PortalHome /></ReturnUrlGate></ProtectedRoute>} />
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
          <Route
            path="/admin/approval-delegation"
            element={<ProtectedRoute><ApprovalDelegation /></ProtectedRoute>}
          />
          {/* Public logout route — clears Portal session, no auth guard */}
          <Route path="/logout" element={<LogoutPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
