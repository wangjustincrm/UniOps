import type { RouteDef } from '@uniops/shell'
import DashboardRouter from '@/pages/dashboard/DashboardRouter'
import TaskInboxPage from '@/pages/tasks/TaskInboxPage'
import PrListPage from '@/pages/pr/PrListPage'
import PrCreatePage from '@/pages/pr/PrCreatePage'
import PrDetailPage from '@/pages/pr/PrDetailPage'
import PrEditPage from '@/pages/pr/PrEditPage'
import PoListPage from '@/pages/po/PoListPage'
import PoCreatePage from '@/pages/po/PoCreatePage'
import PoDetailPage from '@/pages/po/PoDetailPage'
import PoEditPage from '@/pages/po/PoEditPage'
import PoImportedEditPage from '@/pages/po/PoImportedEditPage'
import GrListPage from '@/pages/gr/GrListPage'
import GrCreatePage from '@/pages/gr/GrCreatePage'
import GrDetailPage from '@/pages/gr/GrDetailPage'
import CollectionConfirmPage from '@/pages/gr/CollectionConfirmPage'
import ServiceGrConfirmPage from '@/pages/gr/ServiceGrConfirmPage'
import InvoiceListPage from '@/pages/invoices/InvoiceListPage'
import InvoiceDetailPage from '@/pages/invoices/InvoiceDetailPage'
import AgreementListPage from '@/pages/agreements/AgreementListPage'
import AgreementCreatePage from '@/pages/agreements/AgreementCreatePage'
import AgreementEditPage from '@/pages/agreements/AgreementEditPage'
import AgreementDetailPage from '@/pages/agreements/AgreementDetailPage'
import ReceiptListPage from '@/pages/receipts/ReceiptListPage'
import ReceiptCreatePage from '@/pages/receipts/ReceiptCreatePage'
import ReceiptDetailPage from '@/pages/receipts/ReceiptDetailPage'
import PaListPage from '@/pages/pa/PaListPage'
import PaCreatePage from '@/pages/pa/PaCreatePage'
import PaDetailPage from '@/pages/pa/PaDetailPage'
import PaEditPage from '@/pages/pa/PaEditPage'
import BudgetDashboard from '@/pages/budget/BudgetDashboard'
import BudgetCatalogPage from '@/pages/budget/BudgetCatalogPage'
import BudgetPlanListPage from '@/pages/budget/BudgetPlanListPage'
import BudgetPlanEditPage from '@/pages/budget/BudgetPlanEditPage'
import ReportCentrePage from '@/pages/reports/ReportCentrePage'
import VendorsPage from '@/pages/vendors/VendorsPage'
import ProjectsPage from '@/pages/projects/ProjectsPage'
import PartsListPage from '@/pages/parts/PartsListPage'
import ProfilePage from '@/pages/profile/ProfilePage'
import AdminPanel from '@/pages/admin/AdminPanel'

// Short, distinguishable title for an id-keyed detail/edit tab (UUIDs too long).
const short = (id: string) => (id.length > 8 ? id.slice(0, 8) : id)

export const epmsRoutes: RouteDef[] = [
  { path: '/dashboard', element: <DashboardRouter />, tab: { title: 'Dashboard', icon: 'LayoutDashboard', keyStrategy: 'static', pinned: true } },
  { path: '/tasks', element: <TaskInboxPage />, tab: { title: 'Task Inbox', icon: 'CheckSquare', keyStrategy: 'static' } },

  { path: '/pr', element: <PrListPage />, tab: { title: 'Purchase Requisitions', icon: 'ClipboardList', keyStrategy: 'static' } },
  { path: '/pr/new', element: <PrCreatePage />, tab: { title: 'New PR', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/pr/:id/edit', element: <PrEditPage />, tab: { title: (p) => `Edit PR ${short(p.id)}`, icon: 'ClipboardList', keyStrategy: 'param', paramName: 'id' } },
  { path: '/pr/:id', element: <PrDetailPage />, tab: { title: (p) => `PR ${short(p.id)}`, icon: 'ClipboardList', keyStrategy: 'param', paramName: 'id' } },

  { path: '/po', element: <PoListPage />, tab: { title: 'Purchase Orders', icon: 'Package', keyStrategy: 'static' } },
  { path: '/po/new', element: <PoCreatePage />, tab: { title: 'New PO', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/po/:id/edit', element: <PoEditPage />, tab: { title: (p) => `Edit PO ${short(p.id)}`, icon: 'Package', keyStrategy: 'param', paramName: 'id' } },
  { path: '/po/:id/edit-imported', element: <PoImportedEditPage />, tab: { title: (p) => `Edit Details ${short(p.id)}`, icon: 'Package', keyStrategy: 'param', paramName: 'id' } },
  { path: '/po/:id', element: <PoDetailPage />, tab: { title: (p) => `PO ${short(p.id)}`, icon: 'Package', keyStrategy: 'param', paramName: 'id' } },

  { path: '/gr', element: <GrListPage />, tab: { title: 'Goods Receipt', icon: 'Warehouse', keyStrategy: 'static' } },
  { path: '/gr/new', element: <GrCreatePage />, tab: { title: 'New GR', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/gr/:id/collect', element: <CollectionConfirmPage />, tab: { title: (p) => `Collect ${short(p.id)}`, icon: 'Warehouse', keyStrategy: 'param', paramName: 'id' } },
  { path: '/gr/:id/service-confirm', element: <ServiceGrConfirmPage />, tab: { title: (p) => `Confirm ${short(p.id)}`, icon: 'Warehouse', keyStrategy: 'param', paramName: 'id' } },
  { path: '/gr/:id', element: <GrDetailPage />, tab: { title: (p) => `GR ${short(p.id)}`, icon: 'Warehouse', keyStrategy: 'param', paramName: 'id' } },

  { path: '/invoices', element: <InvoiceListPage />, tab: { title: 'Invoices', icon: 'FileText', keyStrategy: 'static' } },
  { path: '/invoices/:id', element: <InvoiceDetailPage />, tab: { title: (p) => `Invoice ${short(p.id)}`, icon: 'FileText', keyStrategy: 'param', paramName: 'id' } },

  { path: '/agreements', element: <AgreementListPage />, tab: { title: 'Agreements', icon: 'FileSignature', keyStrategy: 'static' } },
  { path: '/agreements/new', element: <AgreementCreatePage />, tab: { title: 'New Agreement', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/agreements/:id/edit', element: <AgreementEditPage />, tab: { title: (p) => `Edit Agreement ${short(p.id)}`, icon: 'FileSignature', keyStrategy: 'param', paramName: 'id' } },
  { path: '/agreements/:id', element: <AgreementDetailPage />, tab: { title: (p) => `Agreement ${short(p.id)}`, icon: 'FileSignature', keyStrategy: 'param', paramName: 'id' } },

  { path: '/receipts', element: <ReceiptListPage />, tab: { title: 'Agreement Receipts', icon: 'Receipt', keyStrategy: 'static' } },
  { path: '/receipts/new', element: <ReceiptCreatePage />, tab: { title: 'New Receipt', icon: 'Plus', keyStrategy: 'static' } },
  // After /receipts/new, matching how /gr/:id and /agreements/:id sit below
  // their own literal sub-paths in this table.
  { path: '/receipts/:id', element: <ReceiptDetailPage />, tab: { title: (p) => `Receipt ${short(p.id)}`, icon: 'Receipt', keyStrategy: 'param', paramName: 'id' } },

  { path: '/pa', element: <PaListPage />, tab: { title: 'Payment Applications', icon: 'CreditCard', keyStrategy: 'static' } },
  { path: '/pa/new', element: <PaCreatePage />, tab: { title: 'New PA', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/pa/create', element: <PaCreatePage />, tab: { title: 'New PA', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/pa/:id/edit', element: <PaEditPage />, tab: { title: (p) => `Edit PA ${short(p.id)}`, icon: 'CreditCard', keyStrategy: 'param', paramName: 'id' } },
  { path: '/pa/:id', element: <PaDetailPage />, tab: { title: (p) => `PA ${short(p.id)}`, icon: 'CreditCard', keyStrategy: 'param', paramName: 'id' } },

  { path: '/budget', element: <BudgetDashboard />, tab: { title: 'Budget Dashboard', icon: 'PiggyBank', keyStrategy: 'static' } },
  { path: '/budget/catalog', element: <BudgetCatalogPage />, tab: { title: 'Account Catalog', icon: 'FolderTree', keyStrategy: 'static' } },
  { path: '/budget/plans', element: <BudgetPlanListPage />, tab: { title: 'Budget Plans', icon: 'ClipboardList', keyStrategy: 'static' } },
  { path: '/budget/plans/:id', element: <BudgetPlanEditPage />, tab: { title: (p) => `Plan ${short(p.id)}`, icon: 'ClipboardList', keyStrategy: 'param', paramName: 'id' } },
  { path: '/reports', element: <ReportCentrePage />, tab: { title: 'Reports', icon: 'FolderOpen', keyStrategy: 'static' } },

  { path: '/vendors', element: <VendorsPage />, tab: { title: 'Vendors', icon: 'Building2', keyStrategy: 'static' } },
  { path: '/projects', element: <ProjectsPage />, tab: { title: 'Projects', icon: 'FolderTree', keyStrategy: 'static' } },
  { path: '/parts', element: <PartsListPage />, tab: { title: 'Parts Catalog', icon: 'Wrench', keyStrategy: 'static' } },

  { path: '/profile', element: <ProfilePage />, tab: { title: 'My Profile', icon: 'User', keyStrategy: 'static' } },
  { path: '/admin', element: <AdminPanel />, tab: { title: 'Admin Panel', icon: 'Settings', keyStrategy: 'static' } },
]
