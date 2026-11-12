import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'
import { AskHost, ToastProvider } from '@/platform-ui'
import { refetchOnWindowFocus } from '@/app/recoverFailedQueries'
import '@/features/common/statusTones'

const queryClient: QueryClient = new QueryClient({
  // Window focus refetches only failed queries (app/recoverFailedQueries.ts).
  defaultOptions: { queries: { refetchOnWindowFocus, retry: 1 } },
  // A write that worked: everything on screen is read again (user 2026-11-12: "I need to refresh to
  // see the new data"): a list, the open record, its history, a summary card, the Health gate.
  // Only the queries in use refetch; the others are just marked stale.
  mutationCache: new MutationCache({ onSuccess: () => queryClient.invalidateQueries() }),
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
      <AskHost />
      </ToastProvider>
    </QueryClientProvider>
  )
}
