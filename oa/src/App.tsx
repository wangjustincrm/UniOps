import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'
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
import TaskListPage from '@/pages/tasks/TaskListPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<Navigate to="/tasks" replace />} />

            {/* Task Inbox */}
            <Route path="/tasks" element={<TaskListPage />} />

            {/* Payment Applications */}
            <Route path="/pa" element={<PaListPage />} />
            <Route path="/pa/new" element={<PaCreatePage />} />
            <Route path="/pa/new/direct" element={<PaDirectCreatePage />} />
            <Route path="/pa/:id/edit" element={<PaDirectEditPage />} />
            <Route path="/pa/:id" element={<PaDetailPage />} />

            {/* Expenses */}
            <Route path="/expenses" element={<ExpenseListPage />} />
            <Route path="/expenses/new/exp" element={<ExpenseCreatePage />} />
            <Route path="/expenses/new/mil" element={<MilCreatePage />} />
            <Route path="/expenses/new/trv" element={<TrvCreatePage />} />
            <Route path="/expenses/new/cfm/:formCode" element={<CfmCreatePage />} />
            <Route path="/expenses/:id" element={<ExpenseDetailPage />} />

            {/* Invoices */}
            <Route path="/invoices" element={<InvoicesPage />} />
            <Route path="/invoices/:source/:id" element={<InvoiceDetailPage />} />

            {/* Admin (system_admin only) */}
            <Route path="/admin/expense-config" element={<ExpenseConfigPage />} />
            <Route path="/admin/custom-forms" element={<CfmAdminPage />} />

            <Route path="*" element={<Navigate to="/tasks" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
