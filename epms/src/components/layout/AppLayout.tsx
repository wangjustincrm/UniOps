import { useEffect, useState } from 'react'
import { Routes, Route } from 'react-router-dom'
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

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

const EPMS_INITIAL_TABS: TabMeta[] = [
  { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', icon: 'LayoutDashboard', pinned: true, closable: false },
]

export function AppLayout() {
  useIdleTimeout()
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const mustChangePassword = useAuthStore((s) => s.mustChangePassword)
  const clearMustChangePassword = useAuthStore((s) => s.clearMustChangePassword)
  const userId = useAuthStore((s) => s.user?.id)
  const [mobileOpen, setMobileOpen] = useState(false)

  const isEmbedded = IS_EMBEDDED

  // Portal owns sign-in for the whole suite (OA/VMS/Finance do the same). Going
  // to EPMS's own /login instead would strand the user on a second login page
  // that Portal knows nothing about. The ?returnUrl= brings them back here.
  //
  // There is deliberately NO local MFA re-check here. EPMS used to gate on a
  // hard-coded 8h `mfaVerifiedAt` window, which ignored Admin → Security: with
  // company MFA switched off, a stale timestamp still pushed the user to /mfa —
  // and MfaPage had no challenge token to work with. Whether MFA is required is
  // the login endpoint's call (company_config.mfa_enabled OR user.mfa_enabled),
  // and LoginPage/MfaPage already honour the mfa_required response.
  useEffect(() => {
    if (!isAuthenticated) {
      const returnUrl = encodeURIComponent(window.location.href)
      window.location.href = `${PORTAL_URL}?returnUrl=${returnUrl}`
    }
  }, [isAuthenticated])

  if (!isAuthenticated) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-neutral-500">
        Redirecting to the UniOps Portal to sign in…
      </div>
    )
  }

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
