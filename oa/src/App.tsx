import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'
import { AssistantMount } from '@/components/AssistantMount'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Default landing → Task Inbox. AppLayout owns all in-app rendering
              via the tab host (route table lives in src/app/routes.tsx). */}
          <Route path="/" element={<Navigate to="/tasks" replace />} />
          <Route path="/*" element={<AppLayout />} />
        </Routes>
        {/* Inside the Router: it reads the location. Outside Routes so it
            survives navigation rather than remounting per page. */}
        <AssistantMount />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
