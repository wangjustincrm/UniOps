import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryObserver, focusManager } from '@tanstack/react-query'

const get = vi.fn(async () => ({ role: 'approver' }))
vi.mock('@/services/dashboard', () => ({ dashboardService: { get: () => get() } }))

describe('dashboard refreshes when the user returns to the tab (DSH-010, ISS-0139)', () => {
  afterEach(() => focusManager.setFocused(undefined))

  it('refetches on focus even though the app turns refetch-on-focus off, and inside staleTime', async () => {
    const { dashboardQueryOptions } = await import('./useDashboard')
    // The app-wide defaults from App.tsx.
    const client = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } })
    client.mount()   // what QueryClientProvider does: subscribes to focus events
    const observer = new QueryObserver(client, dashboardQueryOptions)
    const unsubscribe = observer.subscribe(() => {})
    await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(1))

    focusManager.setFocused(false)
    focusManager.setFocused(true)
    await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2))

    unsubscribe()
    client.unmount()
    client.clear()
  })
})
