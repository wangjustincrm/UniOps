import { useState, useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync, Sidebar as ShellSidebar, Topbar, UserMenu, useSidebarCollapsed } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { mrpRoutes } from '@/app/routes'
import { TrendingUp, PackageSearch, Network, User, SlidersHorizontal, CalendarRange, Truck, ShoppingCart, Boxes } from 'lucide-react'
import { signOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { encodeSession, INVENTORY_URL } from '@/lib/api'
import { useMrpAuth } from '@/store/auth'

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// ── Auth helpers ──────────────────────────────────────────────────────────────

function getStoredSession(): { token: string | null; user: { id?: string; full_name?: string; role?: string } | null } {
  try {
    for (const key of ['mrp-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.token) return { token: state.token, user: state.user ?? null }
    }
    return { token: null, user: null }
  } catch { return { token: null, user: null } }
}

// ── Navigation ────────────────────────────────────────────────────────────────

// `external` items leave MRP: WMS Inventory moved to the Inventory module
// (inventory design §7.1), so its entry links out — to that module's WMS page,
// with the same #__session= handoff Portal uses — and opens in a new tab so
// the planner keeps their MRP tabs.
const NAV: { label: string; href: string; icon: typeof Boxes; external?: boolean }[] = [
  { label: 'Sales Forecast',      href: '/forecast',            icon: TrendingUp       },
  { label: 'Consignment Stock',   href: '/consignment-stock',   icon: PackageSearch    },
  { label: 'Inventory',           href: '/wms',                 icon: Boxes, external: true },
  { label: 'BOM Explorer',        href: '/bom-explorer',        icon: Network          },
  { label: 'Capacity Rules',      href: '/capacity-rules',      icon: SlidersHorizontal },
  { label: 'Production Plan',     href: '/production-plan',     icon: CalendarRange    },
  { label: 'Supply Parameters',   href: '/supply-parameters',   icon: Truck            },
  { label: 'Purchase Suggestions', href: '/purchase-suggestions', icon: ShoppingCart     },
]

// ── Sidebar ───────────────────────────────────────────────────────────────────
// Frame, colours and behaviour come from the Shell; MRP supplies nav + branding.

function Sidebar({ mobileOpen, onClose }: { mobileOpen: boolean; onClose: () => void }) {
  const [collapsed, toggleCollapsed] = useSidebarCollapsed('mrp.sidebar.collapsed')
  const { data: branding } = useBranding()
  const { token, refreshToken, user } = useMrpAuth()
  const session = token && user ? encodeSession(token, refreshToken ?? '', user) : ''
  const externalHref = (path: string) =>
    session ? `${INVENTORY_URL}${path}#__session=${session}` : `${INVENTORY_URL}${path}`

  return (
    <ShellSidebar
      brand={{
        name: branding?.name || 'UniOps',
        tagline: branding?.tagline || 'MRP',
        logoUrl: branding?.logo_data_url || null,
      }}
      sections={[{
        title: 'MRP',
        // External: leaves MRP for the Inventory module, in a new tab, so the
        // planner keeps their MRP tabs.
        items: NAV.map(({ label, href, icon: Icon, external }) => ({
          label, icon: <Icon className="h-4 w-4" />,
          href: external ? externalHref(href) : href,
          external,
        })),
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

const MRP_INITIAL_TABS: TabMeta[] = [
  { key: '/forecast', title: 'Sales Forecast', kind: 'page', path: '/forecast', icon: 'TrendingUp', pinned: true, closable: false },
]

// True when MRP is rendered inside an iframe (e.g. a drill-down tab from
// another module). In that case we hide MRP's own Sidebar/Header/tabs and
// render just the single matched page, so the host app's chrome is the only
// navigation (mirrors OA/EPMS).
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
            {mrpRoutes.map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
          </Routes>
        </div>
      </div>
    )
  }

  return (
    <TabStoreProvider options={{ initialTabs: MRP_INITIAL_TABS, userId: user?.id }}>
      {/* The Shell Sidebar draws its own mobile backdrop. */}
      <div className="relative flex h-screen overflow-hidden bg-canvas">
        <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />

        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <Header onMobileMenuToggle={() => setMobileOpen(v => !v)} />
          <TabRouterSync routes={mrpRoutes} />
          <TabBar />
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={mrpRoutes} pageClassName="mx-auto max-w-[1440px] p-6" />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
}
