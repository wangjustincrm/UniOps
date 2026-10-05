import { useEffect, useRef, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { TabBar, TabHost, TabRouterSync, TabStoreProvider } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import {
  ArrowLeft, BarChart3, CalendarClock, ChevronDown, ChevronLeft, ChevronRight, ClipboardCheck,
  ClipboardList, FileText, FlaskConical, ShieldAlert, ShieldCheck, Siren, Users, Wrench,
  GraduationCap, HeartPulse, List, LogOut, Map, MapPin, Menu, Settings, TriangleAlert, User,
  Mail, MessageSquare,
} from 'lucide-react'
import { ehsRoutes } from '@/app/routes'
import { cn } from '@/lib/utils'
import { signOut } from '@/lib/signOut'
import { useBranding } from '@/hooks/useBranding'
import { usePermissions } from '@/hooks/usePermissions'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

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
  /**
   * Shown *instead of hiding the item* when `permission` is absent. The
   * incident list narrows to your own reports rather than refusing, so a
   * worker still has somewhere to go — the same page, a shorter list. Without
   * this the endpoint's fallback would be unreachable: correct, and invisible.
   */
  labelWithoutPermission?: string
  /**
   * Draws a count beside the entry. Round 5, TC-20: "can an indicator be
   * placed on the word feedback reference item as a flag that there are new
   * messages, like notifications on a phone app."
   *
   * Only one entry uses it today, so it names the thing to count rather than
   * being a generic hook with one caller.
   */
  badge?: 'feedback-unread'
}

/**
 * Twenty entries in one list is a list nobody reads — they scan for the word
 * they want and miss it. Grouped by the work rather than by the part of the
 * system it lives in: somebody looking for the chemical inventory is thinking
 * "what is in the plant", not "the documents subsystem".
 *
 * A group with nothing visible in it is not drawn at all, so a worker sees
 * four headings and a supervisor sees seven — the shape of the menu says what
 * the role is for.
 */
interface NavGroup {
  /** Shown above the group. Replaced by a rule when the sidebar is collapsed,
   *  because a heading with no room for its word is just noise. */
  title: string
  items: NavItem[]
}

// Reporting and my own actions carry no permission: every role holds
// ehs.incident.report, because filing one is the reason a production worker
// opens this module at all.
const NAV: NavGroup[] = [
  {
    // What somebody opens this module to do, in the order they do it. None of
    // the first three carry a permission: every role holds
    // ehs.incident.report, because filing one is the reason a production
    // worker opens this at all.
    title: 'Day to day',
    items: [
      { label: 'Dashboard',          href: '/dashboard',     icon: BarChart3,       permission: 'ehs.incident.read' },
      { label: 'Report an Incident', href: '/report',        icon: TriangleAlert },
      { label: 'My Actions',         href: '/actions/mine',  icon: ClipboardCheck },
      // No permission: filling in an inspection is the same kind of act as
      // filing a report.
      { label: 'Forms',              href: '/forms',         icon: ClipboardList },
      // ★ No permission, and in the first group on purpose. Round 4 asked for
      // "a general comments feedback place"; a place to say something is wrong
      // that only some people can reach is not one.
      { label: 'Feedback',           href: '/feedback',      icon: MessageSquare, badge: 'feedback-unread' },
    ],
  },
  {
    title: 'Incidents and injuries',
    items: [
      { label: 'Incidents',          href: '/incidents',     icon: TriangleAlert,   permission: 'ehs.incident.read', labelWithoutPermission: 'My Reports' },
      { label: 'First Aid Register', href: '/first-aid',     icon: HeartPulse,      permission: 'ehs.incident.read' },
    ],
  },
  {
    title: 'Risk and permits',
    items: [
      { label: 'Hazard Register',    href: '/hazards',       icon: ShieldAlert,     permission: 'ehs.action.read' },
      // No permission: the crew signing a job analysis before starting is
      // every worker, and the list is what they open to find theirs.
      { label: 'Job Analyses',       href: '/jsa',           icon: ClipboardList },
      // Every active role holds ehs.permit.request: the person who needs a
      // tank cleaned is not the person authorised to allow entry into it, and
      // the requester has to be able to raise one.
      { label: 'Work Permits',       href: '/permits',       icon: ShieldCheck,     permission: 'ehs.permit.request' },
    ],
  },
  {
    title: 'Training and talks',
    items: [
      // No permission: a supervisor delivers these and everyone signs one.
      { label: 'Toolbox Talks',      href: '/toolbox-talks', icon: Users },
      { label: 'My Training', href: '/training/mine', icon: GraduationCap },
  { label: 'Training',           href: '/training',      icon: GraduationCap,   permission: 'ehs.training.read' },
      // No permission: taking a quiz is something every worker does, usually
      // by scanning a code the trainer put on the screen.
      { label: 'Quizzes',            href: '/quizzes',       icon: ClipboardList },
    ],
  },
  {
    title: 'The plant',
    items: [
      // Same key as the document library, and every active role holds it:
      // WHMIS makes a worker's access to the data sheet for what they handle
      // a right.
      { label: 'Chemicals',          href: '/chemicals',     icon: FlaskConical,    permission: 'ehs.document.read' },
      { label: 'Safety Equipment',   href: '/equipment',     icon: Wrench,          permission: 'ehs.asset.read' },
      { label: 'Emergency',          href: '/emergency',     icon: Siren,           permission: 'ehs.incident.read' },
    ],
  },
  {
    title: 'Records and compliance',
    items: [
      { label: 'Compliance Calendar', href: '/calendar',     icon: CalendarClock,   permission: 'ehs.incident.read' },
      // Every active role holds ehs.document.read: a policy that cannot be
      // opened cannot be acknowledged, which is what Read & Sign asks people
      // to do.
      { label: 'Documents',          href: '/documents',     icon: FileText,        permission: 'ehs.document.read' },
      { label: 'Committee',          href: '/jhsc',          icon: Users,           permission: 'ehs.jhsc.read' },
      { label: 'Reports',            href: '/reports',       icon: BarChart3,       permission: 'ehs.incident.read' },
      // The built-in auditor role already holds ehs.incident.read along with
      // read access to everything this counts, which is what that role is for.
      { label: 'Audit Evidence',     href: '/audit',         icon: ClipboardCheck,  permission: 'ehs.incident.read' },
    ],
  },
]

const SETTINGS_NAV: NavItem[] = [
  { label: 'Forms',       href: '/settings/forms', icon: ClipboardList, permission: 'ehs.settings.manage' },
  { label: 'Lists',       href: '/settings/lists', icon: List,     permission: 'ehs.settings.manage' },
  { label: 'Plant Areas', href: '/settings/areas', icon: Map,      permission: 'ehs.settings.manage' },
  { label: 'Equipment',   href: '/settings/equipment', icon: Wrench, permission: 'ehs.settings.manage' },
  // Under ehs.worker.write, not settings: a supervisor who maintains who does
  // which job is not the same person who edits vocabularies.
  { label: 'Positions',   href: '/settings/positions', icon: Users, permission: 'ehs.worker.read' },
  { label: 'Area responsibility', href: '/settings/area-owners', icon: MapPin, permission: 'ehs.settings.manage' },
  // The divisor for every injury rate. Without it the dashboard's rate tiles
  // are empty, which is why it sits in the menu rather than inside a report.
  { label: 'Hours Worked', href: '/settings/hours', icon: CalendarClock, permission: 'ehs.settings.manage' },
  { label: 'Who is told', href: '/settings/notify-groups', icon: Mail, permission: 'ehs.settings.manage' },
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

  // ★ How many comments nobody has looked at. Only HSE is counted, because
  // only HSE can read everybody's — and the endpoint says so, so asking as
  // anyone else would be a 403 on every page load.
  //
  // A failed request draws no badge rather than a zero: the page behind it is
  // the truth, and a badge that appeared when the count could not be fetched
  // would be worse than one that is missing.
  const mayTriageFeedback = can('ehs.settings.manage')
  const feedbackCount = useQuery({
    queryKey: ['feedback', 'unread-count'],
    queryFn: () => api.get<{ count: number }>('/api/v1/feedback/unread-count'),
    enabled: mayTriageFeedback,
    // It is a hint, not a register. Polled gently so a comment left while
    // somebody is working shows up without them reloading.
    refetchInterval: 120_000,
    staleTime: 60_000,
  })
  const unreadFeedback = feedbackCount.data?.count ?? 0

  const brandName = branding?.name || 'UniOps'
  const brandTagline = branding?.tagline || 'Safety'
  const brandLogo = branding?.logo_data_url || null
  const brandInitials =
    brandName.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'SF'

  // While permissions are loading, show the entries everybody has rather than
  // flashing the full list and then removing most of it. An item carrying a
  // `labelWithoutPermission` is in that group: it renders under the narrower
  // label until permissions arrive, which may then widen it.
  const visible = (items: NavItem[]) =>
    items.flatMap((n) => {
      if (!n.permission || (!isLoading && can(n.permission))) return [n]
      if (n.labelWithoutPermission) return [{ ...n, label: n.labelWithoutPermission }]
      return []
    })
  const settings = visible(SETTINGS_NAV)
  const groups = NAV
    .map((g) => ({ ...g, items: visible(g.items) }))
    .filter((g) => g.items.length > 0)

  const navLinkCls = (isActive: boolean) => cn(
    'flex items-center gap-2.5 rounded-md px-2 py-2.5 text-sm transition-colors min-h-[44px]',
    isActive ? 'bg-white/15 text-white font-medium' : 'text-white/70 hover:bg-white/10 hover:text-white/95',
    collapsed && 'justify-center px-0',
  )

  // One heading style, used by every group including Settings. Collapsed, the
  // heading becomes a rule: the grouping is still legible without the word.
  const renderHeading = (title: string, first = false) => (collapsed
    ? <div className={cn('border-t border-white/10', first ? 'mb-3' : 'my-3')} />
    : (
      <p className={cn(
        'mb-1 px-2 text-[11px] font-semibold uppercase tracking-wider text-white/40',
        !first && 'mt-4',
      )}>
        {title}
      </p>
    ))

  const renderLink = ({ label, href, icon: Icon, badge }: NavItem) => {
    const count = badge === 'feedback-unread' ? unreadFeedback : 0
    return (
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
        {count > 0 && (
          <span
            className={cn(
              'ml-auto shrink-0 rounded-full bg-danger-600 px-1.5 text-[11px] font-bold',
              'leading-[18px] text-white',
              collapsed && 'absolute right-1 top-1 ml-0 px-1 leading-[16px]',
            )}
            aria-label={`${count} new`}
          >
            {count > 99 ? '99+' : count}
          </span>
        )}
      </NavLink>
    )
  }

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
              <p className="truncate text-[11px] leading-tight text-white/45">{brandTagline}</p>
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
        {/* A group with nothing visible in it is not drawn, heading and all —
            otherwise a worker meets five empty headings and reads the menu as
            broken rather than as shorter. */}
        {groups.map((group, i) => (
          <div key={group.title}>
            {renderHeading(group.title, i === 0)}
            {group.items.map(renderLink)}
          </div>
        ))}

        {settings.length > 0 && (
          <div>
            {renderHeading('Settings')}
            {settings.map(renderLink)}
          </div>
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
          <div className="shrink-0" data-print="hide"><TabBar /></div>
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={ehsRoutes} pageClassName="mx-auto max-w-[1440px]" />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
}
