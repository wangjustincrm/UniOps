import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import { pickSuggestion } from '@/platform-ui/testing'

const api = vi.hoisted(() => ({ preview: vi.fn(), create: vi.fn() }))
vi.mock('./ordersApi', async (orig) => {
  const real = await orig<typeof import('./ordersApi')>()
  return { ...real, ordersClient: { ...real.ordersClient, list: vi.fn(async () => ({ items: [], total: 0 })) },
    previewManual: api.preview, createManual: api.create }
})
vi.mock('../configuration/lookups', () => ({
  manualPartnerOptions: vi.fn(async () => [{ value: 'MAN', label: 'MAN', hint: 'Manual' }]),
  itemOptions: vi.fn(async (q: string) => [{ value: 'CF0092', label: 'CF0092', hint: `match for ${q}` }]),
}))
const { default: ManualOrderPage } = await import('./ManualOrderPage')

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => k.startsWith('sales.')}><ManualOrderPage /></PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Manual order entry (FR-OB-19a)', () => {
  it('previews each line in EA and saves the draft only when every line converts', async () => {
    renderPage()
    const form = await screen.findByRole('region', { name: 'New manual order' })
    await pickSuggestion(within(form).getByRole('combobox', { name: 'Partner' }), 'M', /^MAN/)
    await pickSuggestion(within(form).getByRole('combobox', { name: 'Item 1' }), 'CF00', /^CF0092/)
    fireEvent.change(within(form).getByLabelText('Unit 1'), { target: { value: 'PALLET' } })
    expect(within(form).getByText('Save draft')).toBeDisabled()
    api.preview.mockResolvedValueOnce([{ item_code: 'CF0092', quantity: '1', unit: 'PALLET', qty_ea: null, problem: 'no conversion for unit PALLET' }])
    fireEvent.click(within(form).getByText('Preview EA'))
    expect(await within(form).findByText('no conversion for unit PALLET')).toBeInTheDocument()
    expect(within(form).getByText('Save draft')).toBeDisabled()

    fireEvent.change(within(form).getByLabelText('Unit 1'), { target: { value: 'CASE' } })
    api.preview.mockResolvedValueOnce([{ item_code: 'CF0092', quantity: '1', unit: 'CASE', qty_ea: 6, problem: null }])
    fireEvent.click(within(form).getByText('Preview EA'))
    expect(await within(form).findByText('6')).toBeInTheDocument()
    api.create.mockResolvedValue({ order_number: 201 })
    fireEvent.click(within(form).getByText('Save draft'))
    await waitFor(() => expect(api.create).toHaveBeenCalledWith({ partner_code: 'MAN', customer_reference: null,
      lines: [{ item_code: 'CF0092', quantity: 1, unit: 'CASE' }] }))
  })
})
