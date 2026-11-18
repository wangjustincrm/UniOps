import { useState, useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { CalendarCheck, ListChecks, UserCheck, Plus, ScanLine, User, LayoutDashboard, FileSearch, FileDown, Settings, Inbox, ClipboardCheck } from 'lucide-react'
import { useMyVmsTasks } from '@/services/api'
import { signOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync, Sidebar as ShellSidebar, Topbar, UserMenu, useSidebarCollapsed } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { vmsRoutes } from '@/app/routes'

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// ── Auth helpers ──────────────────────────────────────────────────────────────

function getStoredSession(): { token: string | null; user: { id?: string; full_name?: string; role?: string } | null } {
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

// Frame, colours and behaviour come from the Shell; VMS supplies nav + branding.
function Sidebar({ mobileOpen, onClose }: { mobileOpen: boolean; onClose: () => void }) {
  const [collapsed, toggleCollapsed] = useSidebarCollapsed('vms.sidebar.collapsed')
  const location = useLocation()
  const { user } = getStoredSession()
  const canAudit = !!user?.role && AUDIT_ROLES.has(user.role)
  const canAdmin = !!user?.role && ADMIN_ROLES.has(user.role)
  const { data: tasks } = useMyVmsTasks()
  const taskCount = tasks?.length ?? 0
  const { data: branding } = useBranding()

  // "/" matches Today's Visits exactly (not as a prefix of every route).
  const matchesNav = (href: string) =>
    href === '/' ? location.pathname === '/' : location.pathname.startsWith(href)
  const item = ({ label, href, icon: Icon }: { label: string; href: string; icon: typeof Plus }) => ({
    label, href, icon: <Icon className="h-4 w-4" />, active: matchesNav(href),
    badge: href === '/tasks' && taskCount > 0 ? taskCount : undefined,
  })

  return (
    <ShellSidebar
      brand={{
        name: branding?.name || 'UniOps',
        tagline: branding?.tagline || 'Visitor Management',
        logoUrl: branding?.logo_data_url || null,
      }}
      sections={[
        { title: 'Visits', items: NAV.map(item) },
        ...(canAudit ? [{ title: 'Compliance', items: AUDIT_NAV.map(item) }] : []),
        ...(canAdmin ? [{ title: 'Admin', items: ADMIN_NAV.map(item) }] : []),
      ]}
      collapsed={collapsed}
      onToggleCollapse={toggleCollapsed}
      mobileOpen={mobileOpen}
      onMobileClose={onClose}
      portalUrl={PORTAL_URL}
    />
  )
}

// ── Header ────────────────────────────────────────────────────────────────────

function Header({ onMobileMenuToggle }: { onMobileMenuToggle: () => void }) {
  const { user } = getStoredSession()
  const roleLabel = (user?.role ?? '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  return (
    <Topbar
      onMobileMenu={onMobileMenuToggle}
      className="relative z-10"
      end={
        <UserMenu
          name={user?.full_name ?? 'User'}
          subtitle={roleLabel}
          items={[{ label: 'Portal Home', icon: <User />, href: `${PORTAL_URL}/` }]}
          onSignOut={signOut}
        />
      }
    />
  )
}

// ── AppLayout ─────────────────────────────────────────────────────────────────

const VMS_INITIAL_TABS: TabMeta[] = [
  { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', icon: 'LayoutDashboard', pinned: true, closable: false },
]

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const { token, user } = getStoredSession()

  useEffect(() => {
    if (!token) {
      const returnUrl = encodeURIComponent(window.location.href)
      window.location.href = `${PORTAL_URL}?returnUrl=${returnUrl}`
    }
  }, [token])

  if (!token) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-fg-muted">
        Redirecting to portal for authentication…
      </div>
    )
  }

  return (
    <TabStoreProvider options={{ initialTabs: VMS_INITIAL_TABS, userId: user?.id }}>
      {/* print: — only the page content goes to paper (badges): white background,
          no tab bar, and no fixed-height clipping so multi-page prints are not cut. */}
      <div className="relative flex h-screen overflow-hidden bg-canvas print:h-auto print:overflow-visible print:bg-surface">
        {/* The Shell Sidebar draws its own mobile backdrop and hides in print. */}
        <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />

        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <Header onMobileMenuToggle={() => setMobileOpen(v => !v)} />
          <TabRouterSync routes={vmsRoutes} />
          <div className="shrink-0 print:hidden">
            <TabBar />
          </div>
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={vmsRoutes} pageClassName="mx-auto max-w-[1440px] p-6" />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
}
