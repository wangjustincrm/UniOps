import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'
import { ToastProvider } from '@/platform-ui'
import { refetchOnWindowFocus } from '@/app/recoverFailedQueries'

const queryClient = new QueryClient({
  // Window focus refetches only failed queries (app/recoverFailedQueries.ts).
  defaultOptions: { queries: { refetchOnWindowFocus, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
      <BrowserRouter>
        <Routes>
          {/* AppLayout owns all in-app rendering via the tab host and sends
              "/" to the first nav item the user may see. */}
          <Route path="/*" element={<AppLayout />} />
        </Routes>
      </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}
