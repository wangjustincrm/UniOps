import { useState, useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync, Button, Sidebar as ShellSidebar, Topbar, UserMenu, useSidebarCollapsed } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { inventoryRoutes } from '@/app/routes'
import { Boxes, Database, GitCompare, Route as RouteIcon, Settings, User } from 'lucide-react'
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
// Frame, colours and behaviour come from the Shell; Inventory supplies nav + branding.

function Sidebar({ mobileOpen, onClose }: { mobileOpen: boolean; onClose: () => void }) {
  const [collapsed, toggleCollapsed] = useSidebarCollapsed('inventory.sidebar.collapsed')
  const { data: branding } = useBranding()
  return (
    <ShellSidebar
      brand={{
        name: branding?.name || 'UniOps',
        tagline: branding?.tagline || 'Inventory',
        logoUrl: branding?.logo_data_url || null,
      }}
      sections={[{
        title: 'Inventory',
        items: NAV.map(({ label, href, icon: Icon }) => ({ label, href, icon: <Icon className="h-4 w-4" /> })),
      }]}
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
      <div className="flex h-screen items-center justify-center text-sm text-fg-muted">
        Redirecting to portal for authentication…
      </div>
    )
  }

  // Wait for the permission matrix before deciding what to show.
  if (perms.isLoading) {
    return <div className="flex h-screen items-center justify-center text-sm text-fg-muted">Loading…</div>
  }

  // A failed permission request must not read as "no access" (same reasoning
  // as Finance's AppLayout): with no matrix everything would look revoked
  // when the real cause is the browser never reaching epms-api (e.g. Chrome
  // blocking the call under VPN — local network access).
  if (perms.isError && !perms.data) {
    const unreachable = perms.error instanceof TypeError
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 px-4 text-center text-sm text-fg-muted">
        <p className="font-medium text-fg-secondary">Couldn't load your Inventory permissions.</p>
        <p className="max-w-md">
          {unreachable
            ? "Your browser couldn't reach the UniOps server. Check your network or VPN connection; if Chrome asked about local network access for this site, allow it in Site settings."
            : `The server returned an error: ${perms.error?.message ?? 'unknown error'}`}
        </p>
        <div className="flex items-center gap-4">
          <Button variant="link"
            onClick={() => perms.refetch()}
            disabled={perms.isFetching}
           
          >
            {perms.isFetching ? 'Retrying…' : 'Try again'}
          </Button>
          <a href={PORTAL_URL} className="font-medium text-primary-600 hover:underline">Back to UniOps Portal</a>
        </div>
      </div>
    )
  }

  if (!perms.can(PERM_VIEW)) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 px-4 text-center text-sm text-fg-muted">
        <p>You don't have access to the Inventory module.</p>
        <p className="max-w-md text-xs text-fg-muted">
          Ask an administrator to grant inventory.view in Portal → Access Control.
        </p>
        <a href={PORTAL_URL} className="font-medium text-primary-600 hover:underline">Back to UniOps Portal</a>
      </div>
    )
  }

  // Embedded (iframe): single page, no chrome/tabs — the host owns navigation.
  if (IS_EMBEDDED) {
    return (
      <div className="h-screen overflow-y-auto bg-canvas">
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
      {/* The Shell Sidebar draws its own mobile backdrop. */}
      <div className="relative flex h-screen overflow-hidden bg-canvas">
        <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />

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
