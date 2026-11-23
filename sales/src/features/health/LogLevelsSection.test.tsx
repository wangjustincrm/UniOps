import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { HealthRead, LogOverride } from './healthApi'

const api = vi.hoisted(() => ({ raise: vi.fn(), clear: vi.fn() }))
vi.mock('./healthApi', async (orig) => ({ ...(await orig<typeof import('./healthApi')>()), raiseLogLevel: api.raise, clearLogLevel: api.clear }))
const { LogLevelsSection } = await import('./LogLevelsSection')

const health = (overrides: LogOverride[]) => ({
  log_levels: { default_level: 'INFO', overrides, suggested: { '': 'Everything', 'app.services': 'All of Sales\' own work' },
    effective: {}, max_minutes: 1440, default_minutes: 60, levels: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] },
}) as unknown as HealthRead

function renderAs(keys: string[], h: HealthRead) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => keys.includes(k)}>
    <LogLevelsSection health={h} /></PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Health → Log levels (user 2026-11-23)', () => {
  it('sales.config raises an area for 60 minutes by default, with a reason', async () => {
    api.raise.mockResolvedValue({})
    renderAs(['sales.read', 'sales.config'], health([]))
    expect(screen.getByText('No area is raised.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Raise a log level…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Raise a log level' })
    fireEvent.change(within(dialog).getByLabelText(/Why/), { target: { value: 'Shopify pass fails' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Raise' }))
    await waitFor(() => expect(api.raise).toHaveBeenCalledWith({ logger: 'app.services', level: 'DEBUG', minutes: 60, reason: 'Shopify pass fails' }))
  })

  it('a reader sees what is raised and until when, and no action', () => {
    renderAs(['sales.read'], health([{ logger: 'app.services', level: 'DEBUG', until: '2026-11-23T16:00:00Z', by: 'Op', reason: 'chasing' }]))
    expect(screen.getByRole('table', { name: 'Raised log levels' })).toHaveTextContent('DEBUG')
    expect(screen.queryByRole('button', { name: /Raise a log level/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Back to normal/ })).toBeNull()
  })
})
