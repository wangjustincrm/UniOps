import { useState, useEffect, useRef } from 'react'
import { NavLink, useLocation, Routes, Route } from 'react-router-dom'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { inventoryRoutes } from '@/app/routes'
import {
  Boxes, Database, GitCompare, Route as RouteIcon, Settings,
  ArrowLeft, Menu, ChevronLeft, ChevronRight, LogOut, User, ChevronDown,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { PORTAL_URL } from '@/lib/api'
import { signOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { usePermissions, PERM_VIEW } from '@/hooks/usePermissions'

// ── Auth helpers ──────────────────────────────────────────────────────────────

function getStoredSession(): { token: string | null; user: { id?: string; full_name?: string; role?: string } | null } {
  try {
    for (const key of ['inventory-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.token) return { token: state.token, user: state.user ?? null }
    }
    return { token: null, user: null }
  } catch { return { token: null, user: null } }
}

// ── Navigation ────────────────────────────────────────────────────────────────
// Every page needs inventory.view (cost columns and Settings writes are gated
// inside the pages). Without it the whole module shows a no-access screen —
// see AppLayout below — so the sidebar never has to hide items one by one.

const NAV = [
  { label: 'WMS Inventory',  href: '/wms',            icon: Boxes      },
  { label: 'NC Inventory',   href: '/nc',             icon: Database   },
  { label: 'Reconciliation', href: '/reconciliation', icon: GitCompare },
  { label: 'Trace',          href: '/trace',          icon: RouteIcon  },
  { label: 'Settings',       href: '/settings',       icon: Settings   },
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
  const brandName = branding?.name || 'UniOps'
  const brandTagline = branding?.tagline || 'Inventory'
  const brandLogo = branding?.logo_data_url || null
  const brandInitials = brandName.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'INV'

  const navLinkCls = (isActive: boolean) => cn(
    'flex items-center gap-2.5 rounded-md px-2 py-2.5 text-sm transition-colors min-h-[44px]',
    isActive ? 'bg-white/15 text-white font-medium' : 'text-white/70 hover:bg-white/10 hover:text-white/95',
    collapsed && 'justify-center px-0',
  )

  return (
    <aside className={cn(
      'flex h-screen flex-col bg-primary-700 transition-all duration-200 shrink-0',
      collapsed ? 'w-16' : 'w-60',
      'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:shadow-2xl max-md:w-60',
      mobileOpen ? 'max-md:translate-x-0 max-md:opacity-100' : 'max-md:-translate-x-full max-md:opacity-0',
    )}>
      {/* Brand */}
      <div className="flex h-[60px] items-center justify-between border-b border-white/10 px-3 gap-2">
        {!collapsed && (
          <div className="flex items-center gap-2.5 min-w-0">
            {brandLogo ? (
              <img src={brandLogo} alt={brandName} className="h-8 w-8 shrink-0 rounded-md bg-white object-contain p-0.5" />
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/15">
                <span className="text-xs font-bold text-white">{brandInitials}</span>
              </div>
            )}
            <div className="min-w-0">
              <p className="text-sm font-bold text-white/95 leading-tight truncate">{brandName}</p>
              <p className="text-[10px] text-white/45 leading-tight truncate">{brandTagline}</p>
            </div>
          </div>
        )}
        {collapsed && (
          brandLogo ? (
            <img src={brandLogo} alt={brandName} className="mx-auto h-8 w-8 rounded-md bg-white object-contain p-0.5" />
          ) : (
            <div className="mx-auto flex h-8 w-8 items-center justify-center rounded-md bg-white/15">
              <span className="text-xs font-bold text-white">{brandInitials}</span>
            </div>
          )
        )}
        {/* Desktop collapse toggle — hidden on mobile */}
        <button
          onClick={onToggleCollapse}
          className="hidden md:flex shrink-0 rounded p-1 text-white/50 hover:bg-white/10 hover:text-white/90 transition-colors"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed
            ? <ChevronRight className="h-4 w-4" />
            : <ChevronLeft  className="h-4 w-4" />}
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-4">
        {!collapsed && (
          <p className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-white/40">
            Inventory
          </p>
        )}
        {NAV.map(({ label, href, icon: Icon }) => (
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
        ))}
      </nav>

      {/* Portal link */}
      {!collapsed && (
        <div className="border-t border-white/10 p-3">
          <a
            href={PORTAL_URL}
            className="flex items-center gap-2 rounded-md px-2 py-2 text-sm text-white/60 hover:bg-white/10 hover:text-white/90 transition-colors"
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
  const initials = fullName.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase() || 'U'
  const roleLabel = role.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

  // Close dropdown on outside click
  useEffect(() => {
    if (!menuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuOpen])

  return (
    <header className="flex h-[60px] shrink-0 items-center gap-4 border-b border-neutral-200 bg-white px-4 md:px-6">
      {/* Mobile hamburger */}
      <button
        className="rounded-md p-2 text-neutral-500 hover:bg-neutral-50 md:hidden"
        aria-label="Open menu"
        onClick={onMobileMenuToggle}
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* Spacer */}
      <div className="flex-1" />

      {/* User menu */}
      <div className="relative" ref={menuRef}>
        <button
          onClick={() => setMenuOpen(v => !v)}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-700/10 text-xs font-semibold text-primary-700 ring-2 ring-primary-700/30">
            {initials}
          </div>
          <span className="hidden sm:block font-medium">{fullName}</span>
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
              className="flex w-full items-center gap-2 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors"
            >
              <User className="h-4 w-4" />
              Portal Home
            </a>
            <div className="mt-1 border-t border-neutral-100">
              <button
                onClick={() => { setMenuOpen(false); signOut() }}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-danger-600 hover:bg-danger-50 transition-colors"
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

const INVENTORY_INITIAL_TABS: TabMeta[] = [
  { key: '/nc', title: 'NC Inventory', kind: 'page', path: '/nc', icon: 'Database', pinned: true, closable: false },
]

// True when rendered inside an iframe (a drill-down tab from another module):
// hide the chrome and render just the matched page (mirrors MRP/OA/EPMS).
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
  const perms = usePermissions()

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

  // Wait for the permission matrix before deciding what to show.
  if (perms.isLoading) {
    return <div className="flex h-screen items-center justify-center text-sm text-neutral-500">Loading…</div>
  }

  // A failed permission request must not read as "no access" (same reasoning
  // as Finance's AppLayout): with no matrix everything would look revoked
  // when the real cause is the browser never reaching epms-api (e.g. Chrome
  // blocking the call under VPN — local network access).
  if (perms.isError && !perms.data) {
    const unreachable = perms.error instanceof TypeError
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 px-4 text-center text-sm text-neutral-500">
        <p className="font-medium text-neutral-700">Couldn't load your Inventory permissions.</p>
        <p className="max-w-md">
          {unreachable
            ? "Your browser couldn't reach the UniOps server. Check your network or VPN connection; if Chrome asked about local network access for this site, allow it in Site settings."
            : `The server returned an error: ${perms.error?.message ?? 'unknown error'}`}
        </p>
        <div className="flex items-center gap-4">
          <button
            onClick={() => perms.refetch()}
            disabled={perms.isFetching}
            className="font-medium text-primary-600 hover:underline disabled:opacity-50"
          >
            {perms.isFetching ? 'Retrying…' : 'Try again'}
          </button>
          <a href={PORTAL_URL} className="font-medium text-primary-600 hover:underline">Back to UniOps Portal</a>
        </div>
      </div>
    )
  }

  if (!perms.can(PERM_VIEW)) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 px-4 text-center text-sm text-neutral-500">
        <p>You don't have access to the Inventory module.</p>
        <p className="max-w-md text-xs text-neutral-400">
          Ask an administrator to grant inventory.view in Portal → Access Control.
        </p>
        <a href={PORTAL_URL} className="font-medium text-primary-600 hover:underline">Back to UniOps Portal</a>
      </div>
    )
  }

  // Embedded (iframe): single page, no chrome/tabs — the host owns navigation.
  if (IS_EMBEDDED) {
    return (
      <div className="h-screen overflow-y-auto bg-[#FAFBFC]">
        <div className="mx-auto max-w-[1440px] p-4 md:p-6">
          <Routes>
            {inventoryRoutes.map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
          </Routes>
        </div>
      </div>
    )
  }

  return (
    <TabStoreProvider options={{ initialTabs: INVENTORY_INITIAL_TABS, userId: user?.id }}>
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
          onToggleCollapse={() => setCollapsed(v => !v)}
        />

        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <Header onMobileMenuToggle={() => setMobileOpen(v => !v)} />
          <TabRouterSync routes={inventoryRoutes} />
          <TabBar />
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={inventoryRoutes} pageClassName="mx-auto max-w-[1440px] p-6" />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
}
