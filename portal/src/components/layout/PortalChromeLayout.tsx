/**
 * PortalChromeLayout — the shared sidebar + top header used by every Portal
 * page that isn't the Home screen. Provides consistent navigation between
 * Budget Config, Budget Dashboard/Plans/Catalog (iframe-embedded), and any
 * future Portal-hosted page.
 *
 * Used by:
 *   - portal/src/pages/budget/EpmsEmbed.tsx  (iframe body, scrollableBody=false)
 *   - portal/src/pages/budget/BudgetConfigPage.tsx
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowLeft, Menu, ChevronDown, LogOut,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { EPMS_URL, OA_URL, VMS_URL, encodeSession } from '@/lib/api'
import { globalSignOut } from '@/lib/signOut'
import { cn } from '@/lib/utils'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { PortalSidebar } from './PortalSidebar'

interface PortalChromeLayoutProps {
  /** Sidebar active item key — must match a NAV item href. */
  activeKey: string
  /** Page title in the top header. */
  title: string
  /** Optional subtitle under the title. */
  subtitle?: string
  /** Optional right-side header actions (between title and user menu). */
  headerActions?: React.ReactNode
  /** Main content. */
  children: React.ReactNode
  /** When true (default), main area scrolls vertically. Set false for iframe-only bodies. */
  scrollableBody?: boolean
  /** When true, main area uses tight padding (iframe). Default is comfortable spacing. */
  flushBody?: boolean
}

export function PortalChromeLayout({
  activeKey, title, subtitle, headerActions, children,
  scrollableBody = true, flushBody = false,
}: PortalChromeLayoutProps) {
  const auth = useAuthStore()
  const { data: matrix } = useRolePermissions()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  const session = auth.token && auth.user
    ? encodeSession(auth.token, auth.refreshToken ?? '', auth.user)
    : ''
  const epmsHref = session
    ? `${EPMS_URL}/dashboard#__session=${session}`
    : EPMS_URL
  const oaHref = session
    ? `${OA_URL}/pa#__session=${session}`
    : OA_URL
  const vmsHref = session
    ? `${VMS_URL}/#__session=${session}`
    : VMS_URL

  return (
    <div className="flex h-screen overflow-hidden bg-[#F5F6FA]">
      {mobileOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/50 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      <PortalSidebar
        activeKey={activeKey}
        epmsHref={epmsHref}
        oaHref={oaHref}
        vmsHref={vmsHref}
        session={session}
        userRole={auth.user?.role ?? null}
        matrix={matrix}
        mobileOpen={mobileOpen}
        onClose={() => setMobileOpen(false)}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((v) => !v)}
      />

      <div className="flex flex-1 flex-col overflow-hidden min-w-0">
        <PortalHeader
          title={title}
          subtitle={subtitle}
          actions={headerActions}
          onMobileMenuToggle={() => setMobileOpen((v) => !v)}
        />
        <main
          className={cn(
            'flex-1 min-w-0',
            scrollableBody ? 'overflow-y-auto' : 'overflow-hidden',
            !flushBody && scrollableBody && 'p-4 md:p-6',
          )}
        >
          {children}
        </main>
      </div>
    </div>
  )
}

// ── Header ───────────────────────────────────────────────────────────────────

function PortalHeader({
  title, subtitle, actions, onMobileMenuToggle,
}: {
  title: string
  subtitle?: string
  actions?: React.ReactNode
  onMobileMenuToggle: () => void
}) {
  const { user } = useAuthStore()
  const [menuOpen, setMenuOpen] = useState(false)
  const initials = user?.full_name?.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase() ?? 'U'
  const roleLabel = user?.role?.replace(/_/g, ' ') ?? ''

  return (
    <header className="flex h-[60px] shrink-0 items-center gap-4 border-b border-neutral-200 bg-white px-4 md:px-6">
      <button
        className="rounded-md p-2 text-neutral-500 hover:bg-neutral-50 md:hidden"
        aria-label="Open menu"
        onClick={onMobileMenuToggle}
      >
        <Menu className="h-5 w-5" />
      </button>

      <Link
        to="/"
        className="flex h-9 w-9 items-center justify-center rounded-lg text-neutral-500 hover:bg-neutral-100"
        title="Back to Portal Home"
      >
        <ArrowLeft className="h-4 w-4" />
      </Link>

      <div className="flex-1 min-w-0">
        <h1 className="text-sm font-semibold text-neutral-900 truncate">{title}</h1>
        {subtitle && <p className="text-[11px] text-neutral-500 truncate">{subtitle}</p>}
      </div>

      {actions}

      <div className="relative">
        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-100 text-xs font-semibold text-primary-700 ring-2 ring-primary-400">
            {initials}
          </div>
          <span className="font-medium hidden md:inline">{user?.full_name ?? 'User'}</span>
          <ChevronDown className={cn('h-3.5 w-3.5 transition-transform text-neutral-400', menuOpen && 'rotate-180')} />
        </button>
        {menuOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} aria-hidden="true" />
            <div className="absolute right-0 top-full z-20 mt-1 w-52 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
              <div className="border-b border-neutral-100 px-3 py-2">
                <p className="text-sm font-medium text-neutral-900">{user?.full_name}</p>
                <p className="text-xs text-neutral-500 capitalize">{roleLabel}</p>
              </div>
              <button
                onClick={() => { setMenuOpen(false); globalSignOut() }}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors"
              >
                <LogOut className="h-4 w-4" />
                Sign Out
              </button>
            </div>
          </>
        )}
      </div>
    </header>
  )
}
