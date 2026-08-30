/**
 * Module shell.
 *
 * Mobile-first: the navigation is a bottom bar on a phone and a sidebar from
 * `md` up. Targets are 44px with 8px between them so the module is usable
 * wearing gloves, which is the primary context for this one.
 */
import { NavLink, Route, Routes } from 'react-router-dom'
import { ClipboardCheck, TriangleAlert } from 'lucide-react'
import { ehsRoutes } from '@/app/routes'

const NAV = [
  { to: '/actions/mine', label: 'My actions', Icon: ClipboardCheck },
  { to: '/incidents', label: 'Incidents', Icon: TriangleAlert },
]

function navClass({ isActive }: { isActive: boolean }): string {
  return [
    'flex min-h-[44px] items-center gap-2.5 rounded-lg px-3 text-sm font-medium transition-colors',
    isActive
      ? 'bg-primary-50 text-primary-700'
      : 'text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900',
  ].join(' ')
}

export default function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col bg-neutral-50 md:flex-row">
      <aside className="hidden w-56 shrink-0 border-r border-neutral-200 bg-white p-3 md:block">
        <p className="px-3 pb-3 text-sm font-bold tracking-tight text-neutral-900">Safety</p>
        <nav className="flex flex-col gap-2">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink key={to} to={to} className={navClass}>
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
          <Route path="*" element={<MyActionsRedirect />} />
        </Routes>
      </main>

      {/* Bottom bar on a phone — reachable one-handed. */}
      <nav className="fixed inset-x-0 bottom-0 z-10 flex gap-2 border-t border-neutral-200 bg-white p-2 md:hidden">
        {NAV.map(({ to, label, Icon }) => (
          <NavLink key={to} to={to} className={({ isActive }) =>
            `flex min-h-[44px] flex-1 flex-col items-center justify-center gap-0.5 rounded-lg text-xs font-medium ${
              isActive ? 'bg-primary-50 text-primary-700' : 'text-neutral-600'
            }`
          }>
            <Icon className="h-5 w-5" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}

function MyActionsRedirect() {
  return <MyActionsPageLazy />
}

function MyActionsPageLazy() {
  const route = ehsRoutes.find((r) => r.path === '/actions/mine')
  return <>{route?.element}</>
}
