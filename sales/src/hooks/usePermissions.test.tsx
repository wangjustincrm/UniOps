// Logged out, the layout redirects to the Portal login; asking epms-api for
// the permissions then would only be a 403 in the console (P1).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { epmsApi } from '@/lib/api'
import { usePermissions } from './usePermissions'

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>
}

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('usePermissions', () => {
  it('does not ask without a session', async () => {
    const get = vi.spyOn(epmsApi, 'get').mockResolvedValue({ permissions: {}, roles: [] })
    const { result } = renderHook(() => usePermissions(), { wrapper })
    await new Promise((r) => setTimeout(r, 20))
    expect(get).not.toHaveBeenCalled()
    expect(result.current.can('sales.read')).toBe(false)
  })

  it('asks once a session is stored (the positive half)', async () => {
    localStorage.setItem('sales-auth', JSON.stringify({ state: { token: 't', user: { role: 'requester' } } }))
    const get = vi.spyOn(epmsApi, 'get').mockResolvedValue({ permissions: { 'sales.read': true }, roles: ['requester'] })
    const { result } = renderHook(() => usePermissions(), { wrapper })
    await waitFor(() => expect(result.current.can('sales.read')).toBe(true))
    expect(get).toHaveBeenCalledWith('/api/v1/config/me/permissions')
  })
})
