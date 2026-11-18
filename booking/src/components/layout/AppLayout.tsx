import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { DoorOpen, CalendarDays, Settings, BellRing, LayoutList, Wrench, User, type LucideIcon } from 'lucide-react'
import { signOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { useBookingAuth } from '@/store/auth'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync, deriveTabMeta, Sidebar as ShellSidebar, Topbar, UserMenu, useSidebarCollapsed } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { bookingRoutes } from '@/app/routes'

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// `permission` gates visibility via the EPMS Access Control Matrix.
interface NavItem { label: string; href: string; icon: LucideIcon; permission?: string }
interface NavSection { title: string; items: NavItem[] }

const NAV: NavSection[] = [
  {
    title: 'Booking',
    items: [
      { label: 'Rooms',           href: '/rooms',    icon: DoorOpen,     permission: 'view_booking' },
      { label: 'Meeting Summary', href: '/summary',  icon: CalendarDays, permission: 'view_booking' },
      { label: 'My Bookings',     href: '/my',       icon: CalendarDays, permission: 'view_booking' },
    ],
  },
  {
    title: 'Admin',
    items: [
      { label: 'Rooms Admin',    href: '/admin/rooms',         icon: Wrench,      permission: 'manage_meeting_rooms' },
      { label: 'All Bookings',   href: '/admin/bookings',      icon: LayoutList,  permission: 'manage_meeting_rooms' },
      { label: 'Notifications',  href: '/admin/notifications', icon: BellRing,    permission: 'manage_meeting_rooms' },
      { label: 'Settings',       href: '/admin/settings',      icon: Settings,    permission: 'manage_meeting_rooms' },
    ],
  },
]

type RoleMatrix = Record<string, Record<string, boolean>> | undefined

/** Visible when the role is system_admin or holds the item's permission. */
function isNavItemVisible(item: NavItem, userRole: string | null, matrix: RoleMatrix): boolean {
  if (userRole === 'system_admin') return true
  if (item.permission) return userRole !== null && !!matrix?.[userRole]?.[item.permission]
  return true
}

// ── Auth helpers ──────────────────────────────────────────────────────────────

function getStoredSession(): { token: string | null; user: { id?: string; full_name?: string; role?: string } | null } {
  try {
    for (const key of ['booking-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.token) return { token: state.token, user: state.user ?? null }
    }
    return { token: null, user: null }
  } catch { return { token: null, user: null } }
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

// Frame, colours and behaviour come from the Shell; Booking supplies nav + branding.
function Sidebar({ mobileOpen, onClose }: { mobileOpen: boolean; onClose: () => void }) {
  const [collapsed, toggleCollapsed] = useSidebarCollapsed('booking.sidebar.collapsed')
  const { data: branding } = useBranding()
  const { data: matrix } = useRolePermissions()
  const userRole = useBookingAuth((s) => s.user?.role ?? null)

  const sections = NAV
    .map((section) => ({
      title: section.title,
      items: section.items
        .filter((item) => isNavItemVisible(item, userRole, matrix))
        .map(({ label, href, icon: Icon }) => ({ label, href, icon: <Icon className="h-4 w-4" /> })),
    }))
    .filter((section) => section.items.length > 0)

  return (
    <ShellSidebar
      brand={{
        name: branding?.name || 'UniOps',
        tagline: branding?.tagline || 'Booking',
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

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const isAuthenticated = useBookingAuth((s) => s.isAuthenticated)
  const userId = useBookingAuth((s) => s.user?.id)
  const userRole = useBookingAuth((s) => s.user?.role ?? null)
  const { data: matrix, isLoading: permsLoading } = useRolePermissions()
  const navigate = useNavigate()
  const { pathname } = useLocation()

  useEffect(() => {
    if (!isAuthenticated) {
      const returnUrl = encodeURIComponent(window.location.href)
      window.location.href = `${PORTAL_URL}?returnUrl=${returnUrl}`
    }
  }, [isAuthenticated])

  // First nav item the user can access — landing page + pinned home tab.
  const firstVisible = NAV.flatMap((s) => s.items).find((i) => isNavItemVisible(i, userRole, matrix))
  const homePath = firstVisible?.href

  useEffect(() => {
    if (isAuthenticated && !permsLoading && homePath && pathname === '/') {
      navigate(homePath, { replace: true })
    }
  }, [isAuthenticated, permsLoading, homePath, pathname, navigate])

  if (!isAuthenticated) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-fg-muted">
        Redirecting to portal for authentication…
      </div>
    )
  }

  // Wait for the permission matrix before building the permission-derived shell.
  if (permsLoading) {
    return <div className="flex h-screen items-center justify-center text-sm text-fg-muted">Loading…</div>
  }

  if (!homePath) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 text-sm text-fg-muted">
        <p>No access. </p>
        <a href={PORTAL_URL} className="font-medium text-accent-fg hover:underline">Back to Portal.</a>
      </div>
    )
  }

  const homeMeta = deriveTabMeta(bookingRoutes, homePath)
  const initialTabs: TabMeta[] = homeMeta ? [{ ...homeMeta, pinned: true, closable: false }] : []

  return (
    <TabStoreProvider options={{ initialTabs, userId }}>
      {/* The Shell Sidebar draws its own mobile backdrop; Shell chrome hides when printing. */}
      <div className="relative flex h-screen overflow-hidden bg-canvas">
        <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />

        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <Header onMobileMenuToggle={() => setMobileOpen(v => !v)} />
          <TabRouterSync routes={bookingRoutes} />
          <TabBar />
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={bookingRoutes} pageClassName="h-full" />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
}
