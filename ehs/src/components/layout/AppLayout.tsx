import { useEffect, useRef, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { TabBar, TabHost, TabRouterSync, TabStoreProvider } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import {
  ArrowLeft, CalendarClock, ChevronDown, ChevronLeft, ChevronRight, ClipboardCheck,
  GraduationCap, HeartPulse, List, LogOut, Map, Menu, Settings, TriangleAlert, User,
} from 'lucide-react'
import { ehsRoutes } from '@/app/routes'
import { cn } from '@/lib/utils'
import { signOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { usePermissions } from '@/hooks/usePermissions'

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// ── Auth helpers ──────────────────────────────────────────────────────────────

function getStoredSession(): {
  token: string | null
  user: { id?: string; full_name?: string; role?: string } | null
} {
  try {
    for (const key of ['ehs-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.token) return { token: state.token, user: state.user ?? null }
    }
    return { token: null, user: null }
  } catch {
    return { token: null, user: null }
  }
}

// ── Navigation ────────────────────────────────────────────────────────────────

interface NavItem {
  label: string
  href: string
  icon: typeof TriangleAlert
  permission?: string
}

// Reporting and my own actions carry no permission: every role holds
// ehs.incident.report, because filing one is the reason a production worker
// opens this module at all.
const NAV: NavItem[] = [
  { label: 'Report an Incident', href: '/report',        icon: TriangleAlert },
  { label: 'My Actions',         href: '/actions/mine',  icon: ClipboardCheck },
  { label: 'Incidents',          href: '/incidents',     icon: TriangleAlert,   permission: 'ehs.incident.read' },
  { label: 'First Aid Register', href: '/first-aid',     icon: HeartPulse,      permission: 'ehs.incident.read' },
  { label: 'Compliance Calendar', href: '/calendar',     icon: CalendarClock,   permission: 'ehs.incident.read' },
  { label: 'Training',           href: '/training',      icon: GraduationCap,   permission: 'ehs.training.read' },
]

const SETTINGS_NAV: NavItem[] = [
  { label: 'Lists',       href: '/settings/lists', icon: List,     permission: 'ehs.settings.manage' },
  { label: 'Plant Areas', href: '/settings/areas', icon: Map,      permission: 'ehs.settings.manage' },
  { label: 'Rules',       href: '/settings/rules', icon: Settings, permission: 'ehs.settings.manage' },
]

// ── Sidebar ───────────────────────────────────────────────────────────────────

function Sidebar({
  mobileOpen, onClose, collapsed, onToggleCollapse,
}: {
  mobileOpen: boolean
  onClose: () => void
  collapsed: boolean
  onToggleCollapse: () => void
}) {
  const location = useLocation()
  const { data: branding } = useBranding()
  const { can, isLoading } = usePermissions()

  const brandName = branding?.name || 'UniOps'
  const brandTagline = branding?.tagline || 'Safety'
  const brandLogo = branding?.logo_data_url || null
  const brandInitials =
    brandName.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'SF'

  // While permissions are loading, show the two entries everybody has rather
  // than flashing the full list and then removing most of it.
  const visible = (items: NavItem[]) =>
    items.filter((n) => !n.permission || (!isLoading && can(n.permission)))
  const settings = visible(SETTINGS_NAV)

  const navLinkCls = (isActive: boolean) => cn(
    'flex items-center gap-2.5 rounded-md px-2 py-2.5 text-sm transition-colors min-h-[44px]',
    isActive ? 'bg-white/15 text-white font-medium' : 'text-white/70 hover:bg-white/10 hover:text-white/95',
    collapsed && 'justify-center px-0',
  )

  const renderLink = ({ label, href, icon: Icon }: NavItem) => (
    <NavLink
      key={href}
      to={href}
      onClick={onClose}
      title={collapsed ? label : undefined}
      className={({ isActive }) =>
        cn(navLinkCls(isActive || location.pathname.startsWith(href + '/')), 'relative')
      }
    >
      <Icon className="h-4 w-4 shrink-0" />
      {!collapsed && <span className="truncate">{label}</span>}
    </NavLink>
  )

  return (
    <aside className={cn(
      'flex h-screen flex-col bg-primary-700 transition-all duration-200 shrink-0',
      collapsed ? 'w-16' : 'w-60',
      'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:shadow-2xl max-md:w-60',
      mobileOpen ? 'max-md:translate-x-0 max-md:opacity-100' : 'max-md:-translate-x-full max-md:opacity-0',
    )}>
      {/* Brand */}
      <div className="flex h-[60px] items-center justify-between gap-2 border-b border-white/10 px-3">
        {!collapsed && (
          <div className="flex min-w-0 items-center gap-2.5">
            {brandLogo ? (
              <img src={brandLogo} alt={brandName}
                   className="h-8 w-8 shrink-0 rounded-md bg-white object-contain p-0.5" />
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/15">
                <span className="text-xs font-bold text-white">{brandInitials}</span>
              </div>
            )}
            <div className="min-w-0">
              <p className="truncate text-sm font-bold leading-tight text-white/95">{brandName}</p>
              <p className="truncate text-[10px] leading-tight text-white/45">{brandTagline}</p>
            </div>
          </div>
        )}
        {collapsed && (
          brandLogo ? (
            <img src={brandLogo} alt={brandName}
                 className="mx-auto h-8 w-8 rounded-md bg-white object-contain p-0.5" />
          ) : (
            <div className="mx-auto flex h-8 w-8 items-center justify-center rounded-md bg-white/15">
              <span className="text-xs font-bold text-white">{brandInitials}</span>
            </div>
          )
        )}
        <button
          onClick={onToggleCollapse}
          className="hidden shrink-0 rounded p-1 text-white/50 transition-colors hover:bg-white/10 hover:text-white/90 md:flex"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-4">
        {!collapsed && (
          <p className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-white/40">
            Safety
          </p>
        )}
        {visible(NAV).map(renderLink)}

        {settings.length > 0 && (
          <>
            {!collapsed && (
              <p className="mb-1 mt-4 px-2 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                Settings
              </p>
            )}
            {collapsed && <div className="my-3 border-t border-white/10" />}
            {settings.map(renderLink)}
          </>
        )}
      </nav>

      {/* Portal link */}
      {!collapsed && (
        <div className="border-t border-white/10 p-3">
          <a
            href={PORTAL_URL}
            className="flex items-center gap-2 rounded-md px-2 py-2 text-sm text-white/60 transition-colors hover:bg-white/10 hover:text-white/90"
          >
            <ArrowLeft className="h-4 w-4" />
            <span>Back to UniOps Portal</span>
          </a>
        </div>
      )}
    </aside>
  )
}

// ── Header ────────────────────────────────────────────────────────────────────

function Header({ onMobileMenuToggle }: { onMobileMenuToggle: () => void }) {
  const { user } = getStoredSession()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  const fullName = user?.full_name ?? 'User'
  const role = user?.role ?? ''
  const initials = fullName.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'U'
  const roleLabel = role.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

  useEffect(() => {
    if (!menuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuOpen])

  return (
    <header className="flex h-[60px] shrink-0 items-center gap-4 border-b border-neutral-200 bg-white px-4 md:px-6">
      <button
        className="rounded-md p-2 text-neutral-500 hover:bg-neutral-50 md:hidden"
        aria-label="Open menu"
        onClick={onMobileMenuToggle}
      >
        <Menu className="h-5 w-5" />
      </button>

      <div className="flex-1" />

      <div className="relative" ref={menuRef}>
        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-neutral-700 transition-colors hover:bg-neutral-50"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-700/10 text-xs font-semibold text-primary-700 ring-2 ring-primary-700/30">
            {initials}
          </div>
          <span className="hidden font-medium sm:block">{fullName}</span>
          <ChevronDown className={cn('h-3.5 w-3.5 text-neutral-400 transition-transform', menuOpen && 'rotate-180')} />
        </button>

        {menuOpen && (
          <div className="absolute right-0 top-full z-50 mt-1 w-52 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
            <div className="border-b border-neutral-100 px-3 py-2.5">
              <p className="text-sm font-semibold text-neutral-900">{fullName}</p>
              <p className="text-xs text-neutral-500">{roleLabel}</p>
            </div>
            <a
              href={`${PORTAL_URL}/`}
              onClick={() => setMenuOpen(false)}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm text-neutral-700 transition-colors hover:bg-neutral-50"
            >
              <User className="h-4 w-4" />
              Portal Home
            </a>
            <div className="mt-1 border-t border-neutral-100">
              <button
                onClick={() => { setMenuOpen(false); signOut() }}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-danger-600 transition-colors hover:bg-danger-50"
              >
                <LogOut className="h-4 w-4" />
                Sign Out
              </button>
            </div>
          </div>
        )}
      </div>
    </header>
  )
}

// ── AppLayout ─────────────────────────────────────────────────────────────────

const INITIAL_TABS: TabMeta[] = [
  { key: '/actions/mine', title: 'My Actions', kind: 'page', path: '/actions/mine',
    icon: 'ClipboardCheck', pinned: true, closable: false },
]

// True when Safety is rendered inside an iframe (a drill-down from another
// module). The host app's chrome is then the only navigation, so this module
// hides its own — the same behaviour as EPMS, OA and MRP.
function detectIframeEmbed(): boolean {
  try {
    return typeof window !== 'undefined' && window.self !== window.top
  } catch {
    return true
  }
}
const IS_EMBEDDED = detectIframeEmbed()

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const { token, user } = getStoredSession()

  useEffect(() => {
    if (!token) {
      const returnUrl = encodeURIComponent(window.location.href)
      window.location.href = `${PORTAL_URL}?returnUrl=${returnUrl}`
    }
  }, [token])

  if (!token) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-neutral-500">
        Redirecting to portal for authentication…
      </div>
    )
  }

  if (IS_EMBEDDED) {
    return (
      <div className="h-screen overflow-y-auto bg-[#FAFBFC]">
        <div className="mx-auto max-w-[1440px]">
          <Routes>
            {ehsRoutes.map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
            <Route path="*" element={<Navigate to="/actions/mine" replace />} />
          </Routes>
        </div>
      </div>
    )
  }

  return (
    <TabStoreProvider options={{ initialTabs: INITIAL_TABS, userId: user?.id }}>
      <div className="relative flex h-screen overflow-hidden bg-[#FAFBFC]">
        {mobileOpen && (
          <div
            className="fixed inset-0 z-20 bg-black/50 md:hidden"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
        )}

        <Sidebar
          mobileOpen={mobileOpen}
          onClose={() => setMobileOpen(false)}
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((v) => !v)}
        />

        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Header onMobileMenuToggle={() => setMobileOpen((v) => !v)} />
          <TabRouterSync routes={ehsRoutes} />
          <TabBar />
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={ehsRoutes} pageClassName="mx-auto max-w-[1440px]" />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
}
