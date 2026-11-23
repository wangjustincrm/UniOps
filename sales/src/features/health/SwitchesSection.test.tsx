import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { HealthRead } from './healthApi'

const api = vi.hoisted(() => ({ set: vi.fn() }))
vi.mock('./healthApi', async (orig) => ({ ...(await orig<typeof import('./healthApi')>()), setFeature: api.set }))
const { SwitchesSection } = await import('./SwitchesSection')

const health = { features: [
  { name: 'SALES_PARTNER_WRITES_ENABLED', display_name: 'Partner writes', description: '', env: true, stored: null, effective: true, env_is_ceiling: true },
  { name: 'SALES_EXTERNAL_EMAIL_ENABLED', display_name: 'External email', description: '', env: false, stored: null, effective: false, env_is_ceiling: true },
] } as unknown as HealthRead

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => keys.includes(k)}>
    <SwitchesSection health={health} /></PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Health → Switches (user 2026-11-23)', () => {
  it('sales.config turns a switch off with a reason; a switch the server keeps off offers nothing', async () => {
    api.set.mockResolvedValue([])
    renderAs(['sales.read', 'sales.config'])
    const rows = screen.getAllByRole('row')
    expect(within(rows[2]).queryByRole('button')).toBeNull()  // External email: off on the server, nothing to turn on
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Turn off' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/Why/), { target: { value: 'Radiant is down' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Turn off' }))
    await waitFor(() => expect(api.set).toHaveBeenCalledWith('SALES_PARTNER_WRITES_ENABLED', false, 'Radiant is down'))
  })

  it('a reader sees the switches and no action', () => {
    renderAs(['sales.read'])
    expect(screen.getByRole('table', { name: 'Switches' })).toHaveTextContent('Partner writes')
    expect(screen.queryByRole('button', { name: 'Turn off' })).toBeNull()
  })
})
