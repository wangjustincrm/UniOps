import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'
import PortalHome from '@/pages/PortalHome'
import LoginPage from '@/pages/LoginPage'
import AdminPanel from '@/pages/admin/AdminPanel'
import LogoutPage from '@/pages/LogoutPage'
import BudgetConfigPage from '@/pages/budget/BudgetConfigPage'
import BudgetDashboardPage from '@/pages/budget/BudgetDashboardPage'
import BudgetPlansPage from '@/pages/budget/BudgetPlansPage'
import BudgetCatalogPage from '@/pages/budget/BudgetCatalogPage'
import FactorLibraryPage from '@/pages/budget/FactorLibraryPage'
import DataMaintenance from '@/pages/admin/DataMaintenance'
import CoaConfigPage from '@/pages/finance/CoaConfigPage'
import BankReconciliationPage from '@/pages/finance/BankReconciliationPage'
import PaymentBatchPage from '@/pages/finance/PaymentBatchPage'
import AccountsPayablePage from '@/pages/finance/AccountsPayablePage'
import AccountsReceivablePage from '@/pages/finance/AccountsReceivablePage'
import GeneralLedgerPage from '@/pages/finance/GeneralLedgerPage'
import BankSettingsPage from '@/pages/finance/BankSettingsPage'
import TaxSettingsPage from '@/pages/finance/TaxSettingsPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuthStore()
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuthStore()
  if (isAuthenticated) return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route
            path="/login"
            element={<PublicRoute><LoginPage /></PublicRoute>}
          />
          <Route
            path="/"
            element={<ProtectedRoute><PortalHome /></ProtectedRoute>}
          />
          <Route
            path="/admin"
            element={<ProtectedRoute><AdminPanel /></ProtectedRoute>}
          />
          <Route
            path="/budget/config"
            element={<ProtectedRoute><BudgetConfigPage /></ProtectedRoute>}
          />
          <Route
            path="/finance/coa"
            element={<ProtectedRoute><CoaConfigPage /></ProtectedRoute>}
          />
          <Route
            path="/finance/bank-settings"
            element={<ProtectedRoute><BankSettingsPage /></ProtectedRoute>}
          />
          <Route
            path="/finance/bank"
            element={<ProtectedRoute><BankReconciliationPage /></ProtectedRoute>}
          />
          <Route
            path="/finance/payment-batches"
            element={<ProtectedRoute><PaymentBatchPage /></ProtectedRoute>}
          />
          <Route
            path="/finance/ap"
            element={<ProtectedRoute><AccountsPayablePage /></ProtectedRoute>}
          />
          <Route
            path="/finance/ar"
            element={<ProtectedRoute><AccountsReceivablePage /></ProtectedRoute>}
          />
          <Route
            path="/finance/gl"
            element={<ProtectedRoute><GeneralLedgerPage /></ProtectedRoute>}
          />
          <Route
            path="/finance/tax"
            element={<ProtectedRoute><TaxSettingsPage /></ProtectedRoute>}
          />
          <Route
            path="/budget/dashboard"
            element={<ProtectedRoute><BudgetDashboardPage /></ProtectedRoute>}
          />
          <Route
            path="/budget/plans"
            element={<ProtectedRoute><BudgetPlansPage /></ProtectedRoute>}
          />
          <Route
            path="/budget/catalog"
            element={<ProtectedRoute><BudgetCatalogPage /></ProtectedRoute>}
          />
          <Route
            path="/budget/factors"
            element={<ProtectedRoute><FactorLibraryPage /></ProtectedRoute>}
          />
          <Route
            path="/admin/data-maintenance"
            element={<ProtectedRoute><DataMaintenance /></ProtectedRoute>}
          />
          {/* Public logout route — clears Portal session, no auth guard */}
          <Route path="/logout" element={<LogoutPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
