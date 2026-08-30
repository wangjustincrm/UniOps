/**
 * Module shell.
 *
 * Mobile-first: a bottom bar on a phone, a sidebar from `md` up. Targets are
 * 44px with 8px between them, because the primary context for this module is
 * someone standing on the plant floor wearing gloves.
 *
 * The bottom bar carries four items, not eleven. Reporting and my actions are
 * what a production worker needs; everything else lives behind More, and the
 * settings screens only appear for the people who can change them.
 */
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import {
  CalendarClock, ClipboardCheck, GraduationCap, HeartPulse, List, Map,
  Settings, TriangleAlert,
} from 'lucide-react'
import { ehsRoutes } from '@/app/routes'
import { usePermissions } from '@/hooks/usePermissions'

interface NavItem {
  to: string
  label: string
  Icon: typeof TriangleAlert
  permission?: string
  primary?: boolean
}

const NAV: NavItem[] = [
  { to: '/report', label: 'Report', Icon: TriangleAlert, primary: true },
  { to: '/actions/mine', label: 'My actions', Icon: ClipboardCheck, primary: true },
  { to: '/incidents', label: 'Incidents', Icon: TriangleAlert,
    permission: 'ehs.incident.read', primary: true },
  { to: '/calendar', label: 'Calendar', Icon: CalendarClock,
    permission: 'ehs.incident.read', primary: true },
  { to: '/first-aid', label: 'First aid', Icon: HeartPulse, permission: 'ehs.incident.read' },
  { to: '/training', label: 'Training', Icon: GraduationCap, permission: 'ehs.training.read' },
  { to: '/settings/lists', label: 'Lists', Icon: List, permission: 'ehs.settings.manage' },
  { to: '/settings/areas', label: 'Plant areas', Icon: Map, permission: 'ehs.settings.manage' },
  { to: '/settings/rules', label: 'Rules', Icon: Settings, permission: 'ehs.settings.manage' },
]

export default function AppLayout() {
  const { can, isLoading } = usePermissions()
  // Until permissions arrive, show only what everyone can do rather than
  // flashing links that then disappear.
  const visible = NAV.filter((n) => !n.permission || (!isLoading && can(n.permission)))
  const bottomBar = visible.filter((n) => n.primary).slice(0, 4)

  return (
    <div className="flex min-h-screen flex-col bg-neutral-50 md:flex-row">
      <aside className="hidden w-56 shrink-0 border-r border-neutral-200 bg-white p-3 md:block">
        <p className="px-3 pb-3 text-sm font-bold tracking-tight text-neutral-900">Safety</p>
        <nav className="flex flex-col gap-2">
          {visible.map(({ to, label, Icon }) => (
            <NavLink key={to} to={to} className={sideClass}>
              <Icon className="h-4 w-4" aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="min-w-0 flex-1 pb-20 md:pb-0">
        <Routes>
          {ehsRoutes.map((r) => (
            <Route key={r.path} path={r.path} element={r.element} />
          ))}
          <Route path="/" element={<Navigate to="/actions/mine" replace />} />
          <Route path="*" element={<Navigate to="/actions/mine" replace />} />
        </Routes>
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-10 flex gap-2 border-t border-neutral-200 bg-white p-2 md:hidden">
        {bottomBar.map(({ to, label, Icon }) => (
          <NavLink key={to} to={to} className={bottomClass}>
            <Icon className="h-5 w-5" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}

function sideClass({ isActive }: { isActive: boolean }): string {
  return [
    'flex min-h-[44px] items-center gap-2.5 rounded-lg px-3 text-sm font-medium transition-colors',
    isActive
      ? 'bg-primary-50 text-primary-700'
      : 'text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900',
  ].join(' ')
}

function bottomClass({ isActive }: { isActive: boolean }): string {
  return [
    'flex min-h-[44px] flex-1 flex-col items-center justify-center gap-0.5 rounded-lg text-xs font-medium',
    isActive ? 'bg-primary-50 text-primary-700' : 'text-neutral-600',
  ].join(' ')
}
