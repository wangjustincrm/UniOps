import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AssistantMount } from '@/components/AssistantMount'
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
          {/* AppLayout owns rendering via the tab host and redirects '/' to the
              first page the user can access (route table in src/app/routes.tsx). */}
          <Route path="/*" element={<AppLayout />} />
        </Routes>
        {/* Inside the Router: it reads the location. Outside Routes so it
            survives navigation rather than remounting per page. */}
        <AssistantMount />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
