import type { RouteDef } from '@uniops/shell'
import VisitListPage from '@/pages/VisitListPage'
import VisitCreatePage from '@/pages/VisitCreatePage'
import VisitDetailPage from '@/pages/VisitDetailPage'
import ActiveVisitsPage from '@/pages/ActiveVisitsPage'
import BadgePrintPage from '@/pages/BadgePrintPage'
import CheckOutPage from '@/pages/CheckOutPage'
import DashboardPage from '@/pages/DashboardPage'
import AuditLogPage from '@/pages/AuditLogPage'
import ReportsPage from '@/pages/ReportsPage'
import HealthDeclarationsPage from '@/pages/HealthDeclarationsPage'
import TaskInboxPage from '@/pages/TaskInboxPage'
import VisitorCompliancePage from '@/pages/VisitorCompliancePage'
import AdminPanel from '@/pages/admin/AdminPanel'

export const vmsRoutes: RouteDef[] = [
  { path: '/dashboard', element: <DashboardPage />, tab: { title: 'Dashboard', icon: 'LayoutDashboard', keyStrategy: 'static', pinned: true } },
  { path: '/tasks', element: <TaskInboxPage />, tab: { title: 'Task Inbox', icon: 'Inbox', keyStrategy: 'static' } },
  { path: '/', element: <VisitListPage scope="today" />, tab: { title: 'Today’s Visits', icon: 'CalendarCheck', keyStrategy: 'static' } },
  { path: '/all', element: <VisitListPage scope="all" />, tab: { title: 'All Visits', icon: 'ListChecks', keyStrategy: 'static' } },
  { path: '/active', element: <ActiveVisitsPage />, tab: { title: 'On-Site Now', icon: 'UserCheck', keyStrategy: 'static' } },
  { path: '/new', element: <VisitCreatePage />, tab: { title: 'New Visit', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/check-out', element: <CheckOutPage />, tab: { title: 'Check Out', icon: 'ScanLine', keyStrategy: 'static' } },
  { path: '/audit-log', element: <AuditLogPage />, tab: { title: 'Audit Log', icon: 'FileSearch', keyStrategy: 'static' } },
  { path: '/reports', element: <ReportsPage />, tab: { title: 'Reports', icon: 'FileDown', keyStrategy: 'static' } },
  { path: '/health-declarations', element: <HealthDeclarationsPage />, tab: { title: 'Health Declarations', icon: 'ClipboardCheck', keyStrategy: 'static' } },
  { path: '/admin/*', element: <AdminPanel />, tab: { title: 'VMS Admin', icon: 'Settings', keyStrategy: 'static' } },
  { path: '/visitor/:visitorId/compliance', element: <VisitorCompliancePage />, tab: { title: (p) => `Compliance ${p.visitorId}`, icon: 'ClipboardCheck', keyStrategy: 'param', paramName: 'visitorId' } },
  { path: '/badge/:visitId', element: <BadgePrintPage />, tab: { title: (p) => `Badge ${p.visitId}`, icon: 'Plus', keyStrategy: 'param', paramName: 'visitId' } },
  { path: '/:visitId', element: <VisitDetailPage />, tab: { title: (p) => `Visit ${p.visitId}`, icon: 'CalendarCheck', keyStrategy: 'param', paramName: 'visitId' } },
]
