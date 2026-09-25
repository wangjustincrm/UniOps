import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import AppLayout from '@/components/layout/AppLayout'
import { ApiError } from '@/lib/api'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // ★ A request the server has already answered is not retried. Asking
      // again for a record that does not exist, or that this person may not
      // see, gets the same answer a second later — so the only thing the retry
      // bought was a second of "Loading…" before the truth appeared. Server
      // faults and dropped connections are worth one more try; a verdict is
      // not.
      retry: (count, error) => {
        const status = error instanceof ApiError ? error.status : null
        if (status !== null && status >= 400 && status < 500) return false
        return count < 1
      },
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/*" element={<AppLayout />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
