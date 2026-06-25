import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AppLayout } from '@/components/layout/AppLayout'
import LoginPage from '@/pages/auth/LoginPage'
import MfaPage from '@/pages/auth/MfaPage'
import DashboardRouter from '@/pages/dashboard/DashboardRouter'

// PR pages
import PrListPage from '@/pages/pr/PrListPage'
import PrCreatePage from '@/pages/pr/PrCreatePage'
import PrDetailPage from '@/pages/pr/PrDetailPage'
import PrEditPage from '@/pages/pr/PrEditPage'

// PO pages
import PoListPage from '@/pages/po/PoListPage'
import PoCreatePage from '@/pages/po/PoCreatePage'
import PoDetailPage from '@/pages/po/PoDetailPage'
import PoEditPage from '@/pages/po/PoEditPage'

// GR pages
import GrListPage from '@/pages/gr/GrListPage'
import GrCreatePage from '@/pages/gr/GrCreatePage'
import GrDetailPage from '@/pages/gr/GrDetailPage'
import CollectionConfirmPage from '@/pages/gr/CollectionConfirmPage'
import ServiceGrConfirmPage from '@/pages/gr/ServiceGrConfirmPage'

// Invoice pages
import InvoiceListPage from '@/pages/invoices/InvoiceListPage'
import InvoiceDetailPage from '@/pages/invoices/InvoiceDetailPage'

// PA pages
import PaListPage from '@/pages/pa/PaListPage'
import PaCreatePage from '@/pages/pa/PaCreatePage'
import PaDetailPage from '@/pages/pa/PaDetailPage'
import PaEditPage from '@/pages/pa/PaEditPage'

// Tasks
import TaskInboxPage from '@/pages/tasks/TaskInboxPage'

// Budget
import BudgetDashboard from '@/pages/budget/BudgetDashboard'
import BudgetCatalogPage from '@/pages/budget/BudgetCatalogPage'
import BudgetPlanListPage from '@/pages/budget/BudgetPlanListPage'
import BudgetPlanEditPage from '@/pages/budget/BudgetPlanEditPage'

// Admin
import AdminPanel from '@/pages/admin/AdminPanel'

// Parts
import PartsListPage from '@/pages/parts/PartsListPage'

// Vendors
import VendorsPage from '@/pages/vendors/VendorsPage'

// Projects
import ProjectsPage from '@/pages/projects/ProjectsPage'

// Reports
import ReportCentrePage from '@/pages/reports/ReportCentrePage'

// Profile
import ProfilePage from '@/pages/profile/ProfilePage'

function PlaceholderPage({ title }: { title: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="text-5xl mb-4">🚧</div>
      <h2 className="text-xl font-semibold text-neutral-700">{title}</h2>
      <p className="mt-2 text-sm text-neutral-400">This page is under construction</p>
    </div>
  )
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,  // prevent stale-refetch when switching tabs
      retry: 1,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-white focus:px-4 focus:py-2 focus:text-primary-600"
        >
          Skip to main content
        </a>
        <Routes>
          {/* Public auth routes */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/mfa" element={<MfaPage />} />

          {/* Protected app routes */}
          <Route element={<AppLayout />}>
            <Route path="/dashboard" element={<DashboardRouter />} />
            <Route path="/tasks" element={<TaskInboxPage />} />

            {/* PR */}
            <Route path="/pr" element={<PrListPage />} />
            <Route path="/pr/new" element={<PrCreatePage />} />
            <Route path="/pr/:id" element={<PrDetailPage />} />
            <Route path="/pr/:id/edit" element={<PrEditPage />} />

            {/* PO */}
            <Route path="/po" element={<PoListPage />} />
            <Route path="/po/new" element={<PoCreatePage />} />
            <Route path="/po/:id" element={<PoDetailPage />} />
            <Route path="/po/:id/edit" element={<PoEditPage />} />

            {/* GR */}
            <Route path="/gr" element={<GrListPage />} />
            <Route path="/gr/new" element={<GrCreatePage />} />
            <Route path="/gr/:id" element={<GrDetailPage />} />
            <Route path="/gr/:id/collect" element={<CollectionConfirmPage />} />
            <Route path="/gr/:id/service-confirm" element={<ServiceGrConfirmPage />} />

            {/* Invoices */}
            <Route path="/invoices" element={<InvoiceListPage />} />
            <Route path="/invoices/:id" element={<InvoiceDetailPage />} />

            {/* PA */}
            <Route path="/pa" element={<PaListPage />} />
            <Route path="/pa/new" element={<PaCreatePage />} />
            <Route path="/pa/create" element={<PaCreatePage />} />
            <Route path="/pa/:id" element={<PaDetailPage />} />
            <Route path="/pa/:id/edit" element={<PaEditPage />} />

            {/* Finance */}
            <Route path="/budget" element={<BudgetDashboard />} />
            <Route path="/budget/catalog" element={<BudgetCatalogPage />} />
            <Route path="/budget/plans" element={<BudgetPlanListPage />} />
            <Route path="/budget/plans/:id" element={<BudgetPlanEditPage />} />
            <Route path="/reports" element={<ReportCentrePage />} />

            {/* Master data */}
            <Route path="/vendors" element={<VendorsPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/parts" element={<PartsListPage />} />

            {/* Profile */}
            <Route path="/profile" element={<ProfilePage />} />

            {/* Admin */}
            <Route path="/admin" element={<AdminPanel />} />
          </Route>

          {/* Default redirect */}
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
