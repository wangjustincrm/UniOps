import type { RouteDef } from '@uniops/shell'
import type { ReactNode } from 'react'
import { useOaAuth } from '@/store/auth'
import TaskListPage from '@/pages/tasks/TaskListPage'
import ExpenseListPage from '@/pages/expenses/ExpenseListPage'
import ExpenseCreatePage from '@/pages/expenses/ExpenseCreatePage'
import MilCreatePage from '@/pages/expenses/MilCreatePage'
import TrvCreatePage from '@/pages/expenses/TrvCreatePage'
import CfmCreatePage from '@/pages/expenses/CfmCreatePage'
import ExpenseDetailPage from '@/pages/expenses/ExpenseDetailPage'
import ExpenseEditPage from '@/pages/expenses/ExpenseEditPage'
import InvoicesPage from '@/pages/invoices/InvoicesPage'
import InvoiceDetailPage from '@/pages/invoices/InvoiceDetailPage'
import ExpenseConfigPage from '@/pages/admin/ExpenseConfigPage'
import CfmAdminPage from '@/pages/admin/CfmAdminPage'
import TravelApplicationsListPage from '@/pages/travel/TravelApplicationsListPage'
import TraCreatePage from '@/pages/travel/TraCreatePage'

// Short, distinguishable title for an id-keyed detail tab (UUIDs are too long).
const short = (id: string) => (id.length > 8 ? id.slice(0, 8) : id)

// Roles allowed into the two OA admin pages. Kept in step with the backend
// gates: PATCH /policy and the /expenses/custom-forms writes both accept
// finance_manager as well as system_admin, but this guard only tested for
// system_admin — so a Finance Manager was refused the page whose contents the
// API would have let them save.
//
// Only the PRIMARY role is available here: the JWT carries `role`, and
// additional-role assignments live server-side in user_roles. Someone holding
// finance_manager as an ADDITIONAL role still gets the refusal below even
// though the API would accept their write. Closing that needs the server to
// tell the client what it may do (a /permissions-style endpoint), not a wider
// guess in the browser.
const ADMIN_PAGE_ROLES = ['system_admin', 'finance_manager']

function RequireAdmin({ children }: { children: ReactNode }) {
  const role = useOaAuth((s) => s.user?.role)
  if (!role || !ADMIN_PAGE_ROLES.includes(role)) {
    return <div className="p-8 text-sm text-neutral-500">You don't have access to this page.</div>
  }
  return <>{children}</>
}

export const oaRoutes: RouteDef[] = [
  { path: '/tasks', element: <TaskListPage />, tab: { title: 'Task Inbox', icon: 'CheckSquare', keyStrategy: 'static', pinned: true } },

  // OA's Direct PA is retired (product decision 2026-08-07, confirmed
  // 2026-09-11). The /pa routes, the sidebar entry and the task cards are all
  // gone; api/v1/pa.py refuses creation with a 410 so the absence of a button
  // is not the only thing enforcing it. The page components are left in the
  // tree, unreferenced, so the feature can be restored rather than rewritten —
  // they are simply not reachable.

  { path: '/expenses', element: <ExpenseListPage />, tab: { title: 'Expense Claims', icon: 'Receipt', keyStrategy: 'static' } },
  { path: '/expenses/new/exp', element: <ExpenseCreatePage />, tab: { title: 'New Expense', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/expenses/new/mil', element: <MilCreatePage />, tab: { title: 'New Mileage', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/expenses/new/trv', element: <TrvCreatePage />, tab: { title: 'New Travel', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/expenses/new/cfm/:formCode', element: <CfmCreatePage />, tab: { title: (p) => `New ${p.formCode}`, icon: 'Plus', keyStrategy: 'param', paramName: 'formCode' } },
  // MUST stay above /expenses/:id — react-router picks the more specific of
  // the two, but keeping them adjacent and ordered makes that obvious.
  { path: '/expenses/:id/edit', element: <ExpenseEditPage />, tab: { title: (p) => `Edit ${short(p.id)}`, icon: 'Pencil', keyStrategy: 'param', paramName: 'id' } },
  { path: '/expenses/:id', element: <ExpenseDetailPage />, tab: { title: (p) => `Expense ${short(p.id)}`, icon: 'Receipt', keyStrategy: 'param', paramName: 'id' } },

  { path: '/invoices', element: <InvoicesPage />, tab: { title: 'Invoices', icon: 'FileText', keyStrategy: 'static' } },
  { path: '/invoices/:source/:id', element: <InvoiceDetailPage />, tab: { title: (p) => `Invoice ${short(p.id)}`, icon: 'FileText', keyStrategy: 'param', paramName: 'id' } },

  { path: '/admin/expense-config', element: <RequireAdmin><ExpenseConfigPage /></RequireAdmin>, tab: { title: 'Expense Config', icon: 'Settings', keyStrategy: 'static' } },
  { path: '/admin/custom-forms', element: <RequireAdmin><CfmAdminPage /></RequireAdmin>, tab: { title: 'Custom Forms', icon: 'FileText', keyStrategy: 'static' } },

  { path: '/travel', element: <TravelApplicationsListPage />, tab: { title: 'Travel Applications', icon: 'Plane', keyStrategy: 'static' } },
  { path: '/travel/new', element: <TraCreatePage />, tab: { title: 'New Travel Application', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/travel/:id', element: <ExpenseDetailPage />, tab: { title: (p) => `Travel ${short(p.id)}`, icon: 'Plane', keyStrategy: 'param', paramName: 'id' } },
]
