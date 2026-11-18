import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  CreditCard, Receipt, BookOpen, FileText, Banknote, Landmark, LayoutDashboard, ClipboardList,
  FolderTree, FlaskConical, SlidersHorizontal, Percent, Coins, Settings, User, Scale, Target,
  RefreshCw, Wallet, Download, ShieldAlert, ClipboardCheck, HeartPulse, TrendingUp, CopyX,
  Activity, GitBranch, Network, PieChart, Boxes, Ruler, Table2, type LucideIcon,
} from 'lucide-react'
import { globalSignOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { useAuthStore } from '@/store/auth'
import { TabStoreProvider, TabBar, TabHost, TabRouterSync, deriveTabMeta, Button, Sidebar as ShellSidebar, Topbar, UserMenu, useSidebarCollapsed } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { financeRoutes } from '@/app/routes'

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// `permission` gates visibility via the EPMS Access Control Matrix (same keys the
// old Portal navConfig used). Items hide for users whose role lacks the toggle.
//
// Sections follow the finance function, not the URL prefix: the AP and AR
// subledgers, the GL reporting spine (General), the voucher workbench, budgeting,
// banking, and setup. Order matters — the first item a user can see is also the
// module landing page (see `firstVisible` below), so AP stays first.
interface NavItem { label: string; href: string; icon: LucideIcon; permission?: string }
interface NavSection { title: string; items: NavItem[] }

const NAV: NavSection[] = [
  {
    title: 'AP',
    items: [
      { label: 'Accounts Payable', href: '/finance/ap', icon: CreditCard, permission: 'view_finance' },
      { label: 'AP Reconciliation', href: '/finance/ap-recon', icon: ClipboardCheck, permission: 'view_finance' },
      { label: 'AP Subledger Health', href: '/finance/ap-ledger-health', icon: HeartPulse, permission: 'view_finance' },
      { label: 'AP Duplicate Invoices', href: '/finance/ap-duplicate-invoices', icon: CopyX, permission: 'view_finance' },
      { label: 'AP Cash Flow', href: '/finance/ap-cash-flow', icon: TrendingUp, permission: 'view_finance' },
      { label: 'Payments', href: '/finance/payments', icon: Wallet, permission: 'view_finance' },
      { label: 'Payment Batches', href: '/finance/payment-batches', icon: Banknote, permission: 'view_finance' },
    ],
  },
  {
    title: 'AR',
    items: [
      { label: 'Accounts Receivable', href: '/finance/ar', icon: Receipt, permission: 'view_finance' },
    ],
  },
  {
    title: 'General',
    items: [
      { label: 'General Ledger', href: '/finance/gl', icon: BookOpen, permission: 'view_finance' },
      { label: 'Account Balance', href: '/finance/account-balance', icon: Scale, permission: 'view_finance' },
    ],
  },
  {
    title: 'Voucher',
    items: [
      { label: 'Journal Vouchers', href: '/finance/journal-vouchers', icon: FileText, permission: 'view_finance' },
      { label: 'JV Detail', href: '/finance/jv-lines', icon: Table2, permission: 'view_finance' },
      { label: 'JV Validation', href: '/finance/jv-validation', icon: ShieldAlert, permission: 'view_finance' },
    ],
  },
  {
    title: 'Budget',
    items: [
      { label: 'Budget Dashboard', href: '/budget/dashboard', icon: LayoutDashboard, permission: 'view_budget_dashboard' },
      { label: 'Budget Plans', href: '/budget/plans', icon: ClipboardList, permission: 'view_budget_plans' },
      // Budget vs actual: reads the current approved plan, so it belongs with the
      // plans it compares against even though the page itself lives under /finance.
      { label: 'Budget Actual', href: '/finance/budget-actual', icon: Target, permission: 'view_finance' },
      { label: 'Account Catalog', href: '/budget/catalog', icon: FolderTree, permission: 'view_finance' },
      { label: 'Factor Library', href: '/budget/factors', icon: FlaskConical, permission: 'view_finance' },
      { label: 'Budget Config', href: '/budget/config', icon: SlidersHorizontal, permission: 'view_finance' },
    ],
  },
  {
    title: 'Banking',
    items: [
      { label: 'Bank Reconciliation', href: '/finance/bank', icon: Landmark, permission: 'view_finance' },
      { label: 'Bank Settings', href: '/finance/bank-settings', icon: Settings, permission: 'view_finance' },
    ],
  },
  {
    // Unit costs and margins: its own Access Control key, not view_finance.
    title: 'AI Financial Analysis',
    items: [
      { label: 'Gross Margin', href: '/finance/analysis/margin', icon: PieChart, permission: 'finance.analysis.view' },
      { label: 'Cost Anomalies', href: '/finance/analysis/anomalies', icon: Activity, permission: 'finance.analysis.view' },
      { label: 'Cost Chain', href: '/finance/analysis/chain', icon: Network, permission: 'finance.analysis.view' },
      { label: 'Batch Cost', href: '/finance/analysis/batches', icon: Boxes, permission: 'finance.analysis.view' },
      { label: 'Standard Cost', href: '/finance/analysis/standard', icon: Ruler, permission: 'finance.analysis.view' },
      { label: 'Material Cost Trace', href: '/finance/analysis/trace', icon: GitBranch, permission: 'finance.analysis.view' },
    ],
  },
  {
    title: 'Settings',
    items: [
      { label: 'Chart of Accounts', href: '/finance/coa', icon: FolderTree, permission: 'view_finance' },
      { label: 'Tax Settings', href: '/finance/tax', icon: Percent, permission: 'view_finance' },
      { label: 'Exchange Rates', href: '/finance/fx-rates', icon: Coins, permission: 'view_finance' },
      { label: 'QuickBooks', href: '/finance/qbo', icon: RefreshCw, permission: 'view_finance' },
      // Throwaway: remove with QboCreditImportPage when QuickBooks is retired.
      // Gated on the credit-manage key, not view_finance — this writes spendable
      // credit into the ledger payments are made from, so read access is not enough.
      { label: 'QBO Credit Import', href: '/finance/qbo-credit-import', icon: Download, permission: 'epms.vendor_credit.manage' },
    ],
  },
]

/** Visible when the role is system_admin or holds the item's permission. */
function isNavItemVisible(item: NavItem, userRole: string | null, perms: Record<string, boolean> | undefined): boolean {
  if (userRole === 'system_admin') return true
  if (item.permission) return !!perms?.[item.permission]
  return true
}

// Frame, colours and behaviour come from the Shell; Finance supplies nav + branding.
function Sidebar({ mobileOpen, onClose }: { mobileOpen: boolean; onClose: () => void }) {
  const [collapsed, toggleCollapsed] = useSidebarCollapsed('finance.sidebar.collapsed')
  // Company logo (shared) + the Finance module tagline, both from Company
  // Settings (Portal Admin → Company Settings → Finance tagline).
  const { data: branding } = useBranding('finance')

  // Gate items by the current user's effective permissions (GET /config/me/permissions).
  const perms = useRolePermissions().data?.permissions
  const userRole = useAuthStore((s) => s.user?.role ?? null)

  const sections = NAV
    .map((section) => ({
      title: section.title,
      items: section.items
        .filter((item) => isNavItemVisible(item, userRole, perms))
        .map(({ label, href, icon: Icon }) => ({ label, href, icon: <Icon className="h-4 w-4" /> })),
    }))
    .filter((section) => section.items.length > 0)

  return (
    <ShellSidebar
      brand={{
        name: branding?.name || 'UniOps',
        tagline: branding?.tagline || 'Finance',
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
  const user = useAuthStore((s) => s.user)
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
          onSignOut={globalSignOut}
        />
      }
    />
  )
}

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const userId = useAuthStore((s) => s.user?.id)
  const userRole = useAuthStore((s) => s.user?.role ?? null)
  const {
    data: myPermissions, isLoading: permsLoading, isError: permsFailed,
    error: permsError, refetch: refetchPerms, isFetching: permsRefetching,
  } = useRolePermissions()
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
      <div className="flex h-screen items-center justify-center text-sm text-fg-muted">
        Redirecting to portal for authentication…
      </div>
    )
  }

  // Wait for the permission matrix before building the (permission-derived) shell.
  if (permsLoading) {
    return <div className="flex h-screen items-center justify-center text-sm text-fg-muted">Loading…</div>
  }

  // A failed permission request must not read as "no access": with no matrix
  // every item is hidden, which looked exactly like a revoked role when the
  // real cause was the browser never reaching EPMS (e.g. Chrome blocking the
  // call under VPN — local network access).
  if (permsFailed && !myPermissions) {
    // fetch() rejects with a TypeError when the request never got a response
    // (network down, CORS / local-network-access block); anything else is a
    // server answer and its message is worth showing.
    const unreachable = permsError instanceof TypeError
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 px-4 text-center text-sm text-fg-muted">
        <p className="font-medium text-fg-secondary">Couldn't load your Finance permissions.</p>
        <p className="max-w-md">
          {unreachable
            ? "Your browser couldn't reach the UniOps server. Check your network or VPN connection; if Chrome asked about local network access for this site, allow it in Site settings."
            : `The server returned an error: ${permsError?.message ?? 'unknown error'}`}
        </p>
        <div className="flex items-center gap-4">
          <Button variant="link"
            onClick={() => refetchPerms()}
            disabled={permsRefetching}
           
          >
            {permsRefetching ? 'Retrying…' : 'Try again'}
          </Button>
          <a href={PORTAL_URL} className="font-medium text-accent-fg hover:underline">Back to UniOps Portal</a>
        </div>
      </div>
    )
  }

  if (!homePath) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 text-sm text-fg-muted">
        <p>You don't have access to the Finance module.</p>
        <a href={PORTAL_URL} className="font-medium text-accent-fg hover:underline">Back to UniOps Portal</a>
      </div>
    )
  }

  const homeMeta = deriveTabMeta(financeRoutes, homePath)
  const initialTabs: TabMeta[] = homeMeta ? [{ ...homeMeta, pinned: true, closable: false }] : []

  return (
    <TabStoreProvider options={{ initialTabs, userId }}>
      {/* The Shell Sidebar draws its own mobile backdrop. */}
      <div className="relative flex h-screen overflow-hidden bg-canvas">
        <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
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
