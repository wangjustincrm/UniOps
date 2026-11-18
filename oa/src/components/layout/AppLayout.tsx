import { useState, useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync, Sidebar as ShellSidebar, Topbar, UserMenu, useSidebarCollapsed } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { oaRoutes } from '@/app/routes'
import { useQuery } from '@tanstack/react-query'
import { Receipt, FileText, Settings, User, CheckSquare, Plane } from 'lucide-react'
import { api } from '@/lib/api'
import { signOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { TabQueryScope, TabRefreshButton } from './TabQueryScope'

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// List and detail tabs re-read their data when the user comes back to them and
// get a Refresh button beside the tab bar (as EPMS, UI-TAB-004). The Task Inbox
// has its own Refresh button beside its title (as EPMS, DSH-021), so it is not
// listed here. Create / edit forms and the admin settings forms are left out on
// purpose — see TabQueryScope.
const REFRESHABLE_ROUTES = new Set(['/expenses', '/expenses/:id', '/travel', '/travel/:id'])
const tabRoutes = oaRoutes.map((r) => REFRESHABLE_ROUTES.has(r.path)
  ? { ...r, element: <TabQueryScope routes={oaRoutes}>{r.element}</TabQueryScope> }
  : r)

// ── Auth helpers ──────────────────────────────────────────────────────────────

function getStoredSession(): { token: string | null; user: { id?: string; full_name?: string; role?: string } | null } {
  try {
    for (const key of ['oa-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.token) return { token: state.token, user: state.user ?? null }
    }
    return { token: null, user: null }
  } catch { return { token: null, user: null } }
}

// ── Navigation ────────────────────────────────────────────────────────────────

const NAV = [
  { label: 'Task Inbox',            href: '/tasks',    icon: CheckSquare },
  { label: 'Expense Claims',        href: '/expenses', icon: Receipt     },
  { label: 'Travel Applications',   href: '/travel',   icon: Plane       },
]

const ADMIN_NAV = [
  { label: 'Expense Config', href: '/admin/expense-config', icon: Settings  },
  { label: 'Custom Forms',   href: '/admin/custom-forms',   icon: FileText  },
]

// ── Task count hook ───────────────────────────────────────────────────────────

const ACTION_TYPES = new Set([
  'approve_expense', 'approve_pa',
  'revise_expense', 'revise_pa',
  'pay_expense', 'pay_pa',
])

function useActionCount(): number {
  const { data } = useQuery<{ items: Array<{ task_type: string }> }>({
    queryKey: ['oa-tasks'],
    queryFn: () => api.get('/api/v1/tasks'),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  return (data?.items ?? []).filter(t => ACTION_TYPES.has(t.task_type)).length
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
// Frame, colours and behaviour come from the Shell; OA supplies nav + branding.

function Sidebar({ mobileOpen, onClose }: { mobileOpen: boolean; onClose: () => void }) {
  const [collapsed, toggleCollapsed] = useSidebarCollapsed('oa.sidebar.collapsed')
  const { user } = getStoredSession()
  const isAdmin = user?.role === 'system_admin'
  const actionCount = useActionCount()
  const { data: branding } = useBranding()

  const sections = [
    {
      title: 'Modules',
      items: NAV.map(({ label, href, icon: Icon }) => ({
        label, href, icon: <Icon className="h-4 w-4" />,
        badge: href === '/tasks' && actionCount > 0 ? actionCount : undefined,
      })),
    },
    ...(isAdmin ? [{
      title: 'Admin',
      items: ADMIN_NAV.map(({ label, href, icon: Icon }) => ({ label, href, icon: <Icon className="h-4 w-4" /> })),
    }] : []),
  ]

  return (
    <ShellSidebar
      brand={{
        name: branding?.name || 'UniOps',
        tagline: branding?.tagline || 'Expense & Payment',
        logoUrl: branding?.logo_data_url || null,
      }}
      sections={sections}
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
  const role = user?.role ?? ''
  const roleLabel = role.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

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

const OA_INITIAL_TABS: TabMeta[] = [
  { key: '/tasks', title: 'Task Inbox', kind: 'page', path: '/tasks', icon: 'CheckSquare', pinned: true, closable: false },
]

// True when OA is rendered inside an iframe (e.g. a Finance drill-down tab). In
// that case we hide OA's own Sidebar/Header/tabs and render just the single
// matched page, so the host app's chrome is the only navigation (mirrors EPMS).
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

  // Embedded (iframe): single page, no chrome/tabs — the host owns navigation.
  if (IS_EMBEDDED) {
    return (
      <div className="h-screen overflow-y-auto bg-canvas">
        <div className="mx-auto max-w-[1440px] p-4 md:p-6">
          <Routes>
            {oaRoutes.map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
          </Routes>
        </div>
      </div>
    )
  }

  return (
    <TabStoreProvider options={{ initialTabs: OA_INITIAL_TABS, userId: user?.id }}>
      {/* The Shell Sidebar draws its own mobile backdrop. */}
      <div className="relative flex h-screen overflow-hidden bg-canvas">
        <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />

        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <Header onMobileMenuToggle={() => setMobileOpen(v => !v)} />
          <TabRouterSync routes={oaRoutes} />
          {/* The tab strip scrolls sideways only (no ▲▼ arrows beside it). */}
          <div className="flex shrink-0">
            <div className="min-w-0 flex-1 [&>div]:overflow-y-hidden"><TabBar /></div>
            <TabRefreshButton />
          </div>
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={tabRoutes} pageClassName="mx-auto max-w-[1440px] p-6" />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
}
