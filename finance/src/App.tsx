import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Default landing → Accounts Payable. AppLayout owns rendering via the
              tab host (route table lives in src/app/routes.tsx). */}
          <Route path="/" element={<Navigate to="/finance/ap" replace />} />
          <Route path="/*" element={<AppLayout />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
