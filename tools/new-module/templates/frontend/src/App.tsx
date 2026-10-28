import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'
%%#platform%%
import { ToastProvider } from '@/platform-ui'
%%/platform%%

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
%%#platform%%
      <ToastProvider>
%%/platform%%
      <BrowserRouter>
        <Routes>
          {/* AppLayout owns all in-app rendering via the tab host and sends
              "/" to the first nav item the user may see. */}
          <Route path="/*" element={<AppLayout />} />
        </Routes>
      </BrowserRouter>
%%#platform%%
      </ToastProvider>
%%/platform%%
    </QueryClientProvider>
  )
}
