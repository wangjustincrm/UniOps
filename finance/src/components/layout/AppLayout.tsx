import { useState, useEffect, useRef } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  CreditCard, Receipt, BookOpen, FileText, Banknote, Landmark, LayoutDashboard,
  ClipboardList, FolderTree, FlaskConical, SlidersHorizontal, Percent, Settings,
  ArrowLeft, Menu, ChevronLeft, ChevronRight, LogOut, User, ChevronDown, Scale, Target,
  RefreshCw, Wallet,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { globalSignOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { useAuthStore } from '@/store/auth'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync, deriveTabMeta } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { financeRoutes } from '@/app/routes'

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// `permission` gates visibility via the EPMS Access Control Matrix (same keys the
// old Portal navConfig used). Items hide for users whose role lacks the toggle.
interface NavItem { label: string; href: string; icon: LucideIcon; permission?: string }
interface NavSection { title: string; items: NavItem[] }

const NAV: NavSection[] = [
  {
    title: 'Finance',
    items: [
      { label: 'Accounts Payable', href: '/finance/ap', icon: CreditCard, permission: 'view_finance' },
      { label: 'Accounts Receivable', href: '/finance/ar', icon: Receipt, permission: 'view_finance' },
      { label: 'General Ledger', href: '/finance/gl', icon: BookOpen, permission: 'view_finance' },
      { label: 'Journal Vouchers', href: '/finance/journal-vouchers', icon: FileText, permission: 'view_finance' },
      { label: 'Account Balance', href: '/finance/account-balance', icon: Scale, permission: 'view_finance' },
      { label: 'Budget Actual', href: '/finance/budget-actual', icon: Target, permission: 'view_finance' },
      { label: 'Payments', href: '/finance/payments', icon: Wallet, permission: 'view_finance' },
      { label: 'Payment Batches', href: '/finance/payment-batches', icon: Banknote, permission: 'view_finance' },
      { label: 'Bank Reconciliation', href: '/finance/bank', icon: Landmark, permission: 'view_finance' },
      { label: 'QuickBooks', href: '/finance/qbo', icon: RefreshCw, permission: 'view_finance' },
    ],
  },
  {
    title: 'Budget',
    items: [
      { label: 'Budget Dashboard', href: '/budget/dashboard', icon: LayoutDashboard, permission: 'view_budget_dashboard' },
      { label: 'Budget Plans', href: '/budget/plans', icon: ClipboardList, permission: 'view_budget_plans' },
      { label: 'Account Catalog', href: '/budget/catalog', icon: FolderTree, permission: 'view_finance' },
      { label: 'Factor Library', href: '/budget/factors', icon: FlaskConical, permission: 'view_finance' },
      { label: 'Budget Config', href: '/budget/config', icon: SlidersHorizontal, permission: 'view_finance' },
    ],
  },
  {
    title: 'Settings',
    items: [
      { label: 'Chart of Accounts', href: '/finance/coa', icon: FolderTree, permission: 'view_finance' },
      { label: 'Tax Settings', href: '/finance/tax', icon: Percent, permission: 'view_finance' },
      { label: 'Bank Settings', href: '/finance/bank-settings', icon: Settings, permission: 'view_finance' },
    ],
  },
]

/** Visible when the role is system_admin or holds the item's permission. */
function isNavItemVisible(item: NavItem, userRole: string | null, perms: Record<string, boolean> | undefined): boolean {
  if (userRole === 'system_admin') return true
  if (item.permission) return !!perms?.[item.permission]
  return true
}

function Sidebar({ mobileOpen, onClose, collapsed, onToggleCollapse }: {
  mobileOpen: boolean; onClose: () => void; collapsed: boolean; onToggleCollapse: () => void
}) {
  const location = useLocation()
  // Company logo (shared) + the Finance module tagline, both from Company
  // Settings (Portal Admin → Company Settings → Finance tagline).
  const { data: branding } = useBranding('finance')
  const brandName = branding?.name || 'UniOps'
  const brandTagline = branding?.tagline || 'Finance'
  const brandLogo = branding?.logo_data_url || null
  const initials = brandName.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'FI'

  // Gate items by the current user's effective permissions (GET /config/me/permissions).
  const perms = useRolePermissions().data?.permissions
  const userRole = useAuthStore((s) => s.user?.role ?? null)
  const isVisible = (item: NavItem) => isNavItemVisible(item, userRole, perms)

  const navLinkCls = (active: boolean) => cn(
    'flex items-center gap-2.5 rounded-md px-2 py-2.5 text-sm transition-colors min-h-[44px]',
    active ? 'bg-white/15 text-white font-medium' : 'text-white/70 hover:bg-white/10 hover:text-white/95',
    collapsed && 'justify-center px-0',
  )

  return (
    <aside className={cn(
      'flex h-screen flex-col bg-[#085E5E] transition-all duration-200 shrink-0',
      collapsed ? 'w-16' : 'w-60',
      'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:shadow-2xl max-md:w-60',
      mobileOpen ? 'max-md:translate-x-0 max-md:opacity-100' : 'max-md:-translate-x-full max-md:opacity-0',
    )}>
      <div className="flex h-[60px] items-center justify-between border-b border-white/10 px-3 gap-2">
        {!collapsed && (
          <div className="flex items-center gap-2.5 min-w-0">
            {brandLogo ? (
              <img src={brandLogo} alt={brandName} className="h-8 w-8 shrink-0 rounded-md bg-white object-contain p-0.5" />
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/15">
                <span className="text-xs font-bold text-white">{initials}</span>
              </div>
            )}
            <div className="min-w-0">
              <p className="text-sm font-bold text-white/95 leading-tight truncate">{brandName}</p>
              <p className="text-[10px] text-white/45 leading-tight truncate">{brandTagline}</p>
            </div>
          </div>
        )}
        <button
          onClick={onToggleCollapse}
          className="hidden md:flex shrink-0 rounded p-1 text-white/50 hover:bg-white/10 hover:text-white/90 transition-colors"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-4">
        {NAV.map((section) => {
          const items = section.items.filter(isVisible)
          if (items.length === 0) return null
          return (
          <div key={section.title} className="mb-4">
            {!collapsed && (
              <p className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-white/40">{section.title}</p>
            )}
            {collapsed && <div className="my-2 border-t border-white/10" />}
            {items.map(({ label, href, icon: Icon }) => (
              <NavLink
                key={href}
                to={href}
                onClick={onClose}
                title={collapsed ? label : undefined}
                className={() => navLinkCls(location.pathname === href)}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!collapsed && <span className="truncate">{label}</span>}
              </NavLink>
            ))}
          </div>
          )
        })}
      </nav>

      {!collapsed && (
        <div className="border-t border-white/10 p-3">
          <a href={PORTAL_URL} className="flex items-center gap-2 rounded-md px-2 py-2 text-sm text-white/60 hover:bg-white/10 hover:text-white/90 transition-colors">
            <ArrowLeft className="h-4 w-4" />
            <span>Back to UniOps Portal</span>
          </a>
        </div>
      )}
    </aside>
  )
}

function Header({ onMobileMenuToggle }: { onMobileMenuToggle: () => void }) {
  const user = useAuthStore((s) => s.user)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const fullName = user?.full_name ?? 'User'
  const initials = fullName.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'U'
  const roleLabel = (user?.role ?? '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

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
      <button className="rounded-md p-2 text-neutral-500 hover:bg-neutral-50 md:hidden" aria-label="Open menu" onClick={onMobileMenuToggle}>
        <Menu className="h-5 w-5" />
      </button>
      <div className="flex-1" />
      <div className="relative" ref={menuRef}>
        <button onClick={() => setMenuOpen((v) => !v)} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-100 text-xs font-semibold text-primary-700 ring-2 ring-primary-400">{initials}</div>
          <span className="hidden sm:block font-medium">{fullName}</span>
          <ChevronDown className={cn('h-3.5 w-3.5 text-neutral-400 transition-transform', menuOpen && 'rotate-180')} />
        </button>
        {menuOpen && (
          <div className="absolute right-0 top-full z-50 mt-1 w-52 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
            <div className="border-b border-neutral-100 px-3 py-2.5">
              <p className="text-sm font-semibold text-neutral-900">{fullName}</p>
              <p className="text-xs text-neutral-500">{roleLabel}</p>
            </div>
            <a href={`${PORTAL_URL}/`} onClick={() => setMenuOpen(false)} className="flex w-full items-center gap-2 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors">
              <User className="h-4 w-4" /> Portal Home
            </a>
            <div className="mt-1 border-t border-neutral-100">
              <button onClick={() => { setMenuOpen(false); globalSignOut() }} className="flex w-full items-center gap-2 px-3 py-2 text-sm text-danger-600 hover:bg-danger-50 transition-colors">
                <LogOut className="h-4 w-4" /> Sign Out
              </button>
            </div>
          </div>
        )}
      </div>
    </header>
  )
}

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const userId = useAuthStore((s) => s.user?.id)
  const userRole = useAuthStore((s) => s.user?.role ?? null)
  const { data: myPermissions, isLoading: permsLoading } = useRolePermissions()
  const perms = myPermissions?.permissions
  const navigate = useNavigate()
  const { pathname } = useLocation()

  useEffect(() => {
    if (!isAuthenticated) {
      const returnUrl = encodeURIComponent(window.location.href)
      window.location.href = `${PORTAL_URL}?returnUrl=${returnUrl}`
    }
  }, [isAuthenticated])

  // First nav item the user can access — landing page + pinned home tab. Avoids
  // landing on a page (e.g. Accounts Payable) the user has no permission for.
  const firstVisible = NAV.flatMap((s) => s.items).find((i) => isNavItemVisible(i, userRole, perms))
  const homePath = firstVisible?.href

  useEffect(() => {
    if (isAuthenticated && !permsLoading && homePath && pathname === '/') {
      navigate(homePath, { replace: true })
    }
  }, [isAuthenticated, permsLoading, homePath, pathname, navigate])

  if (!isAuthenticated) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-neutral-500">
        Redirecting to portal for authentication…
      </div>
    )
  }

  // Wait for the permission matrix before building the (permission-derived) shell.
  if (permsLoading) {
    return <div className="flex h-screen items-center justify-center text-sm text-neutral-500">Loading…</div>
  }

  if (!homePath) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 text-sm text-neutral-500">
        <p>You don't have access to the Finance module.</p>
        <a href={PORTAL_URL} className="font-medium text-primary-600 hover:underline">Back to UniOps Portal</a>
      </div>
    )
  }

  const homeMeta = deriveTabMeta(financeRoutes, homePath)
  const initialTabs: TabMeta[] = homeMeta ? [{ ...homeMeta, pinned: true, closable: false }] : []

  return (
    <TabStoreProvider options={{ storageKey: 'uniops:finance:tabs:v2', initialTabs, userId }}>
      <div className="relative flex h-screen overflow-hidden bg-[#FAFBFC]">
        {mobileOpen && (
          <div className="fixed inset-0 z-20 bg-black/50 md:hidden" onClick={() => setMobileOpen(false)} aria-hidden="true" />
        )}
        <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} collapsed={collapsed} onToggleCollapse={() => setCollapsed((v) => !v)} />
        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <Header onMobileMenuToggle={() => setMobileOpen((v) => !v)} />
          <TabRouterSync routes={financeRoutes} />
          <TabBar />
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={financeRoutes} pageClassName="h-full" />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
}
