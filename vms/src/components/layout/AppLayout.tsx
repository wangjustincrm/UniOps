import { useState, useEffect, useRef } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  CalendarCheck, ListChecks, UserCheck, Plus, ArrowLeft, Menu, ScanLine,
  ChevronLeft, ChevronRight, LogOut, User, ChevronDown,
  LayoutDashboard, FileSearch, FileDown, Settings, Inbox, ClipboardCheck,
} from 'lucide-react'
import { useMyVmsTasks } from '@/services/api'
import { cn } from '@/lib/utils'
import { signOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { vmsRoutes } from '@/app/routes'

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// ── Auth helpers ──────────────────────────────────────────────────────────────

function getStoredSession(): { token: string | null; user: { full_name?: string; role?: string } | null } {
  try {
    for (const key of ['vms-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.token) return { token: state.token, user: state.user ?? null }
    }
    return { token: null, user: null }
  } catch { return { token: null, user: null } }
}

// ── Navigation ────────────────────────────────────────────────────────────────

const NAV = [
  { label: 'Dashboard',      href: '/dashboard', icon: LayoutDashboard },
  { label: 'Task Inbox',     href: '/tasks',     icon: Inbox           },
  { label: 'Today’s Visits', href: '/',          icon: CalendarCheck   },
  { label: 'All Visits',     href: '/all',       icon: ListChecks      },
  { label: 'On-Site Now',    href: '/active',    icon: UserCheck       },
  { label: 'New Visit',      href: '/new',       icon: Plus            },
  { label: 'Check Out',      href: '/check-out', icon: ScanLine        },
]

const AUDIT_NAV = [
  { label: 'Audit Log',           href: '/audit-log',           icon: FileSearch     },
  { label: 'Reports',             href: '/reports',             icon: FileDown       },
  { label: 'Health Declarations', href: '/health-declarations', icon: ClipboardCheck },
]

const ADMIN_NAV = [
  { label: 'VMS Admin',      href: '/admin',     icon: Settings        },
]

const AUDIT_ROLES = new Set(['auditor', 'system_admin'])
const ADMIN_ROLES = new Set(['system_admin'])

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
  const { user } = getStoredSession()
  const canAudit = !!user?.role && AUDIT_ROLES.has(user.role)
  const canAdmin = !!user?.role && ADMIN_ROLES.has(user.role)
  const { data: tasks } = useMyVmsTasks()
  const taskCount = tasks?.length ?? 0
  const { data: branding } = useBranding()
  const brandName = branding?.name || 'UniOps'
  const brandTagline = branding?.tagline || 'Visitor Management'
  const brandLogo = branding?.logo_data_url || null
  const brandInitials = brandName.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'VMS'

  const navLinkCls = (isActive: boolean) => cn(
    'flex items-center gap-2.5 rounded-md px-2 py-2.5 text-sm transition-colors min-h-[44px]',
    isActive ? 'bg-white/15 text-white font-medium' : 'text-white/70 hover:bg-white/10 hover:text-white/95',
    collapsed && 'justify-center px-0',
  )

  // "/" matches Today's Visits exactly (not as a prefix of every route).
  const matchesNav = (href: string) =>
    href === '/' ? location.pathname === '/' : location.pathname.startsWith(href)

  return (
    <aside className={cn(
      'no-print flex h-screen flex-col bg-[#085E5E] transition-all duration-200 shrink-0',
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
              <span className="text-[10px] font-bold text-white">{brandInitials}</span>
            </div>
          )
        )}
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
            Visits
          </p>
        )}
        {NAV.map(({ label, href, icon: Icon }) => (
          <NavLink
            key={href}
            to={href}
            onClick={onClose}
            end={href === '/'}
            title={collapsed ? label : undefined}
            className={() => navLinkCls(matchesNav(href))}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {!collapsed && <span className="truncate flex-1">{label}</span>}
            {href === '/tasks' && taskCount > 0 && (
              <span className={cn(
                'flex h-5 min-w-5 items-center justify-center rounded-full bg-primary-400 px-1.5 text-[10px] font-bold text-white',
                collapsed && 'absolute right-1 top-1',
              )}>
                {taskCount > 99 ? '99+' : taskCount}
              </span>
            )}
          </NavLink>
        ))}

        {canAudit && (
          <>
            {!collapsed && (
              <p className="mt-4 mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                Compliance
              </p>
            )}
            {collapsed && <div className="my-2 border-t border-white/10" />}
            {AUDIT_NAV.map(({ label, href, icon: Icon }) => (
              <NavLink
                key={href}
                to={href}
                onClick={onClose}
                title={collapsed ? label : undefined}
                className={() => navLinkCls(matchesNav(href))}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!collapsed && <span className="truncate">{label}</span>}
              </NavLink>
            ))}
          </>
        )}

        {canAdmin && (
          <>
            {!collapsed && (
              <p className="mt-4 mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-white/40">
                Admin
              </p>
            )}
            {collapsed && <div className="my-2 border-t border-white/10" />}
            {ADMIN_NAV.map(({ label, href, icon: Icon }) => (
              <NavLink
                key={href}
                to={href}
                onClick={onClose}
                title={collapsed ? label : undefined}
                className={() => navLinkCls(matchesNav(href))}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!collapsed && <span className="truncate">{label}</span>}
              </NavLink>
            ))}
          </>
        )}
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
    <header className="no-print flex h-[60px] shrink-0 items-center gap-4 border-b border-neutral-200 bg-white px-4 md:px-6">
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
          onClick={() => setMenuOpen(v => !v)}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[#085E5E]/10 text-xs font-semibold text-[#085E5E] ring-2 ring-[#085E5E]/30">
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
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors"
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

const VMS_INITIAL_TABS: TabMeta[] = [
  { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', icon: 'LayoutDashboard', pinned: true, closable: false },
]

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const { token } = getStoredSession()

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

  return (
    <TabStoreProvider options={{ storageKey: 'uniops:vms:tabs', initialTabs: VMS_INITIAL_TABS }}>
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
          <TabRouterSync routes={vmsRoutes} />
          <TabBar />
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={vmsRoutes} pageClassName="mx-auto max-w-[1440px] p-6" />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
}
