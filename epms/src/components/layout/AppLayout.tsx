import { useState } from 'react'
import { Navigate, Routes, Route } from 'react-router-dom'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { Sidebar } from './Sidebar'
import { Header, ForcedChangePasswordModal } from './Header'
import { useAuthStore } from '@/stores/auth.store'
import { useIdleTimeout } from '@/hooks/useIdleTimeout'
import { epmsRoutes } from '@/app/routes'

// True when this app is rendered inside an iframe (e.g. Portal's budget shell).
// In that case we hide EPMS's own Sidebar/Header AND the tab bar so Portal's
// chrome is the only navigation. Detected once at module load.
function detectIframeEmbed(): boolean {
  try {
    return typeof window !== 'undefined' && window.self !== window.top
  } catch {
    // Cross-origin parent access can throw — treat as embedded.
    return true
  }
}

const IS_EMBEDDED = detectIframeEmbed()

const EPMS_INITIAL_TABS: TabMeta[] = [
  { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', icon: 'LayoutDashboard', pinned: true, closable: false },
]

export function AppLayout() {
  useIdleTimeout()
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const mfaVerifiedAt   = useAuthStore((s) => s.mfaVerifiedAt)
  const mustChangePassword = useAuthStore((s) => s.mustChangePassword)
  const clearMustChangePassword = useAuthStore((s) => s.clearMustChangePassword)
  const userId = useAuthStore((s) => s.user?.id)
  const isMfaValid = mfaVerifiedAt !== null && Date.now() - mfaVerifiedAt < 8 * 60 * 60 * 1000
  const [mobileOpen, setMobileOpen] = useState(false)

  const isEmbedded = IS_EMBEDDED

  if (!isAuthenticated) return <Navigate to="/login" replace />
  if (!isMfaValid) return <Navigate to="/mfa" replace />

  // Embedded (iframe): single page, no chrome, no tabs — Portal owns navigation.
  if (isEmbedded) {
    return (
      <div className="flex h-screen flex-col overflow-hidden bg-[#FAFBFC]">
        <main id="main-content" className="flex-1 overflow-y-auto p-6" tabIndex={-1}>
          <div className="mx-auto max-w-[1440px]">
            <Routes>
              {epmsRoutes.map((r) => (
                <Route key={r.path} path={r.path} element={r.element} />
              ))}
            </Routes>
          </div>
        </main>
        {mustChangePassword && (
          <ForcedChangePasswordModal onDone={clearMustChangePassword} />
        )}
      </div>
    )
  }

  // Normal: full chrome + keep-alive multi-tab workspace.
  return (
    <TabStoreProvider options={{ initialTabs: EPMS_INITIAL_TABS, userId }}>
      <div className="flex h-screen overflow-hidden bg-[#FAFBFC] relative">
        {/* Mobile overlay backdrop */}
        {mobileOpen && (
          <div
            className="fixed inset-0 z-20 bg-neutral-900/50 backdrop-blur-sm md:hidden animate-[fadeIn_0.15s_ease-out]"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
        )}

        <Sidebar mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />

        <div className="flex flex-1 flex-col overflow-hidden">
          <Header onMobileMenuToggle={() => setMobileOpen((v) => !v)} />
          <TabRouterSync routes={epmsRoutes} />
          <TabBar />
          <main id="main-content" className="relative flex-1 overflow-hidden" tabIndex={-1}>
            <TabHost routes={epmsRoutes} pageClassName="mx-auto max-w-[1440px] p-6" />
          </main>
        </div>

        {mustChangePassword && (
          <ForcedChangePasswordModal onDone={clearMustChangePassword} />
        )}
      </div>
    </TabStoreProvider>
  )
}
