import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'

const api = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn() }))
vi.mock('./ordersApi', async (orig) => ({ ...(await orig<typeof import('./ordersApi')>()),
  fetchManualOrderSettings: api.get, saveManualOrderSettings: api.save }))
const { ManualOrderSettingsButton } = await import('./ManualOrderSettingsButton')

const SETTINGS = { express_offered: true, ltl_from_lb: 150, reasons: ['Gift', 'Sample', 'Other'], free_reasons: ['Gift', 'Sample'] }

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => keys.includes(k)}>
    <ManualOrderSettingsButton /></PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Orders → Manual order settings (review 2026-11-19)', () => {
  it('shows the reasons and which are free goods; saving sends them (sales.config)', async () => {
    api.get.mockResolvedValue(SETTINGS)
    api.save.mockImplementation(async (s) => s)
    renderAs(['sales.read', 'sales.config'])
    fireEvent.click(screen.getByRole('button', { name: 'Manual order settings' }))
    const dialog = await screen.findByRole('dialog', { name: 'Manual order settings' })
    expect(await within(dialog).findByText('Free goods')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Offer Purolator Express' }))
    fireEvent.click(within(dialog).getByRole('button', { name: /Save/ }))
    await waitFor(() => expect(api.save).toHaveBeenCalledWith({ ...SETTINGS, express_offered: false }))
  })

  it('a reader sees them and cannot save', async () => {
    api.get.mockResolvedValue(SETTINGS)
    renderAs(['sales.read'])
    fireEvent.click(screen.getByRole('button', { name: 'Manual order settings' }))
    const dialog = await screen.findByRole('dialog', { name: 'Manual order settings' })
    await within(dialog).findByText('Free goods')
    expect(within(dialog).queryByRole('button', { name: /Save/ })).toBeNull()
  })
})
