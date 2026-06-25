import { useState } from 'react'
import { Navigate, Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Header, ForcedChangePasswordModal } from './Header'
import { useAuthStore } from '@/stores/auth.store'
import { useIdleTimeout } from '@/hooks/useIdleTimeout'

// True when this app is rendered inside an iframe (e.g. Portal's budget shell).
// In that case we hide EPMS's own Sidebar/Header so Portal's chrome is the only
// visible navigation. Detected once at module load; survives internal navigation.
function detectIframeEmbed(): boolean {
  try {
    return typeof window !== 'undefined' && window.self !== window.top
  } catch {
    // Cross-origin parent access can throw — treat as embedded.
    return true
  }
}

const IS_EMBEDDED = detectIframeEmbed()

export function AppLayout() {
  useIdleTimeout()
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const mfaVerifiedAt   = useAuthStore((s) => s.mfaVerifiedAt)
  const mustChangePassword = useAuthStore((s) => s.mustChangePassword)
  const clearMustChangePassword = useAuthStore((s) => s.clearMustChangePassword)
  const isMfaValid = mfaVerifiedAt !== null && Date.now() - mfaVerifiedAt < 8 * 60 * 60 * 1000
  const [mobileOpen, setMobileOpen] = useState(false)

  const isEmbedded = IS_EMBEDDED

  if (!isAuthenticated) return <Navigate to="/login" replace />
  if (!isMfaValid) return <Navigate to="/mfa" replace />

  if (isEmbedded) {
    return (
      <div className="flex h-screen flex-col overflow-hidden bg-[#FAFBFC]">
        <main id="main-content" className="flex-1 overflow-y-auto p-6" tabIndex={-1}>
          <div className="mx-auto max-w-[1440px]">
            <Outlet />
          </div>
        </main>
        {mustChangePassword && (
          <ForcedChangePasswordModal onDone={clearMustChangePassword} />
        )}
      </div>
    )
  }

  return (
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
        <main
          id="main-content"
          className="flex-1 overflow-y-auto p-6"
          tabIndex={-1}
        >
          <div className="mx-auto max-w-[1440px]">
            <Outlet />
          </div>
        </main>
      </div>

      {mustChangePassword && (
        <ForcedChangePasswordModal onDone={clearMustChangePassword} />
      )}
    </div>
  )
}
