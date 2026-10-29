import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { PartnerRead } from './partnersApi'

const partner: PartnerRead = {
  id: 1, code: 'WMT', name: 'Walmart Canada', abbrev: null, channel: 'TRUECOMMERCE', settlement_mode: 'INVOICE',
  sending_mode: 'AUTOMATIC', delay_minutes: 0, invoice_method: '810_JSON', invoice_email: null, invoice_cc: null,
  payment_terms: null, dc_routing_required: true, min_shelf_life_days: null, label_code: null, carrier_defaults: null,
  active: true, version: 2, created_at: '2026-10-29T12:00:00Z', created_by_name: 'Op', updated_at: null, updated_by_name: null,
}

vi.mock('./partnersApi', async (orig) => {
  const real = await orig<typeof import('./partnersApi')>()
  return {
    ...real,
    partnersClient: {
      ...real.partnersClient,
      list: vi.fn(async () => ({ items: [partner], total: 1 })),
      update: vi.fn(),
    },
    fetchPartnerAudit: vi.fn(async () => [
      { id: 2, at: '2026-10-29T13:00:00Z', user_name: 'Op', action: 'update', before: { name: 'Walmart' }, after: { name: 'Walmart Canada' }, reason: 'update' },
      { id: 1, at: '2026-10-29T12:00:00Z', user_name: 'Op', action: 'create', before: null, after: { code: 'WMT' }, reason: 'create' },
    ]),
  }
})

const { default: PartnersPage } = await import('./PartnersPage')

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <PermissionProvider can={(k) => keys.includes(k)}>
          <PartnersPage />
        </PermissionProvider>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

async function openPartner() {
  fireEvent.click(await screen.findByText('Walmart Canada'))
  return screen.findByRole('dialog')
}

describe('Partners page', () => {
  it('a reader without sales.config sees the list and a read-only partner with its history', async () => {
    renderAs(['sales.read'])
    expect(await screen.findByText('Walmart Canada')).toBeInTheDocument()
    expect(screen.getByText('Export CSV')).toBeInTheDocument()
    expect(screen.queryByText('New')).not.toBeInTheDocument()
    expect(screen.queryByText('Import CSV')).not.toBeInTheDocument()

    const sheet = await openPartner()
    expect(within(sheet).getAllByText(/read only/).length).toBeGreaterThan(0)
    expect(within(sheet).getByLabelText(/^Name/)).toBeDisabled()
    expect(within(sheet).queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(within(sheet).queryByRole('button', { name: 'Deactivate' })).not.toBeInTheDocument()
    const history = await within(sheet).findByRole('region', { name: 'History' })
    await waitFor(() => expect(within(history).getByText(/Walmart → Walmart Canada/)).toBeInTheDocument())
    expect(within(history).getByText('Created')).toBeInTheDocument()
  })

  it('with sales.config the same partner opens for editing (the admitted half)', async () => {
    renderAs(['sales.read', 'sales.config'])
    expect(await screen.findByText('New')).toBeInTheDocument()
    expect(screen.getByText('Import CSV')).toBeInTheDocument()
    const sheet = await openPartner()
    expect(within(sheet).queryAllByText(/read only/)).toHaveLength(0)
    expect(within(sheet).getByLabelText(/^Name/)).toBeEnabled()
    expect(within(sheet).getByLabelText(/^Code/)).toBeDisabled()  // immutable after creation
    expect(within(sheet).getByRole('button', { name: 'Save' })).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Deactivate' })).toBeInTheDocument()
  })
})
