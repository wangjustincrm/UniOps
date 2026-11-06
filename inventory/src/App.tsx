import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'

// No AssistantMount: the AI assistant is out of scope for this phase (design D18).

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Default landing → NC Inventory (WMS Inventory is still a placeholder
              until phase 3). AppLayout owns all in-app rendering via the tab
              host (route table lives in src/app/routes.tsx). */}
          <Route path="/" element={<Navigate to="/nc" replace />} />
          <Route path="/*" element={<AppLayout />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
