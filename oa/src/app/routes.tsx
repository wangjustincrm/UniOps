import type { RouteDef } from '@uniops/shell'
import TaskListPage from '@/pages/tasks/TaskListPage'
import PaListPage from '@/pages/pa/PaListPage'
import PaDetailPage from '@/pages/pa/PaDetailPage'
import PaCreatePage from '@/pages/pa/PaCreatePage'
import PaDirectCreatePage from '@/pages/pa/PaDirectCreatePage'
import PaDirectEditPage from '@/pages/pa/PaDirectEditPage'
import ExpenseListPage from '@/pages/expenses/ExpenseListPage'
import ExpenseCreatePage from '@/pages/expenses/ExpenseCreatePage'
import MilCreatePage from '@/pages/expenses/MilCreatePage'
import TrvCreatePage from '@/pages/expenses/TrvCreatePage'
import CfmCreatePage from '@/pages/expenses/CfmCreatePage'
import ExpenseDetailPage from '@/pages/expenses/ExpenseDetailPage'
import InvoicesPage from '@/pages/invoices/InvoicesPage'
import InvoiceDetailPage from '@/pages/invoices/InvoiceDetailPage'
import ExpenseConfigPage from '@/pages/admin/ExpenseConfigPage'
import CfmAdminPage from '@/pages/admin/CfmAdminPage'
import TravelApplicationsListPage from '@/pages/travel/TravelApplicationsListPage'
import TraCreatePage from '@/pages/travel/TraCreatePage'

// Short, distinguishable title for an id-keyed detail tab (UUIDs are too long).
const short = (id: string) => (id.length > 8 ? id.slice(0, 8) : id)

export const oaRoutes: RouteDef[] = [
  { path: '/tasks', element: <TaskListPage />, tab: { title: 'Task Inbox', icon: 'CheckSquare', keyStrategy: 'static', pinned: true } },

  { path: '/pa', element: <PaListPage />, tab: { title: 'Payment Applications', icon: 'CreditCard', keyStrategy: 'static' } },
  { path: '/pa/new', element: <PaCreatePage />, tab: { title: 'New PA', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/pa/new/direct', element: <PaDirectCreatePage />, tab: { title: 'New Direct PA', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/pa/:id/edit', element: <PaDirectEditPage />, tab: { title: (p) => `Edit PA ${short(p.id)}`, icon: 'CreditCard', keyStrategy: 'param', paramName: 'id' } },
  { path: '/pa/:id', element: <PaDetailPage />, tab: { title: (p) => `PA ${short(p.id)}`, icon: 'CreditCard', keyStrategy: 'param', paramName: 'id' } },

  { path: '/expenses', element: <ExpenseListPage />, tab: { title: 'Expense Claims', icon: 'Receipt', keyStrategy: 'static' } },
  { path: '/expenses/new/exp', element: <ExpenseCreatePage />, tab: { title: 'New Expense', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/expenses/new/mil', element: <MilCreatePage />, tab: { title: 'New Mileage', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/expenses/new/trv', element: <TrvCreatePage />, tab: { title: 'New Travel', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/expenses/new/cfm/:formCode', element: <CfmCreatePage />, tab: { title: (p) => `New ${p.formCode}`, icon: 'Plus', keyStrategy: 'param', paramName: 'formCode' } },
  { path: '/expenses/:id', element: <ExpenseDetailPage />, tab: { title: (p) => `Expense ${short(p.id)}`, icon: 'Receipt', keyStrategy: 'param', paramName: 'id' } },

  { path: '/invoices', element: <InvoicesPage />, tab: { title: 'Invoices', icon: 'FileText', keyStrategy: 'static' } },
  { path: '/invoices/:source/:id', element: <InvoiceDetailPage />, tab: { title: (p) => `Invoice ${short(p.id)}`, icon: 'FileText', keyStrategy: 'param', paramName: 'id' } },

  { path: '/admin/expense-config', element: <ExpenseConfigPage />, tab: { title: 'Expense Config', icon: 'Settings', keyStrategy: 'static' } },
  { path: '/admin/custom-forms', element: <CfmAdminPage />, tab: { title: 'Custom Forms', icon: 'FileText', keyStrategy: 'static' } },

  { path: '/travel', element: <TravelApplicationsListPage />, tab: { title: 'Travel Applications', icon: 'Plane', keyStrategy: 'static' } },
  { path: '/travel/new', element: <TraCreatePage />, tab: { title: 'New Travel Application', icon: 'Plus', keyStrategy: 'static' } },
]
