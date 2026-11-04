import { openTab } from '@/platform-ui/testing'
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
  return { qc, ...render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <PermissionProvider can={(k) => keys.includes(k)}>
          <PartnersPage />
        </PermissionProvider>
      </ToastProvider>
    </QueryClientProvider>,
  ) }
}

async function openPartner() {
  fireEvent.click(await screen.findByText('Walmart Canada'))
  return screen.findByRole('dialog')
}

describe('Partners page', () => {
  it('a reader without sales.config sees the list and a read-only partner with its history', async () => {
    renderAs(['sales.read'])
    expect(await screen.findByText('Walmart Canada')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeInTheDocument()  // an icon with its tooltip
    expect(screen.queryByText('New')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Import CSV' })).not.toBeInTheDocument()

    const sheet = await openPartner()
    expect(within(sheet).getAllByText(/read only/i).length).toBeGreaterThan(0)
    expect(within(sheet).getByLabelText(/^Name/)).toBeDisabled()
    expect(within(sheet).queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(within(sheet).queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument()  // no Deactivate
    await openTab(sheet, 'History')
    const history = await within(sheet).findByRole('region', { name: 'History' })
    await waitFor(() => expect(within(history).getByText(/Walmart → Walmart Canada/)).toBeInTheDocument())
    expect(within(history).getByText('Created')).toBeInTheDocument()
  })

  it('with sales.config the same partner opens for editing (the admitted half)', async () => {
    renderAs(['sales.read', 'sales.config'])
    expect(await screen.findByText('New')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Import CSV' })).toBeInTheDocument()
    const sheet = await openPartner()
    expect(within(sheet).queryAllByText(/read only/i)).toHaveLength(0)
    expect(within(sheet).getByLabelText(/^Name/)).toBeEnabled()
    expect(within(sheet).getByLabelText(/^Code/)).toBeDisabled()  // immutable after creation
    expect(within(sheet).getByRole('button', { name: 'Save' })).toBeInTheDocument()
    fireEvent.keyDown(within(sheet).getByRole('button', { name: 'More actions' }), { key: 'Enter' })  // Deactivate is under More
    expect(await screen.findByRole('menuitem', { name: 'Deactivate' })).toBeInTheDocument()
  })
})

describe('the dev2 partners (regression, 2026-10-29)', () => {
  it('lists all seven and opens one whose updated_at is null', async () => {
    const { DEV2_PARTNERS } = await import('./dev2Partners.fixture')
    const mod = await import('./partnersApi')
    vi.mocked(mod.partnersClient.list).mockResolvedValue({ items: DEV2_PARTNERS, total: DEV2_PARTNERS.length })
    renderAs(['sales.read', 'sales.config'])
    for (const p of DEV2_PARTNERS) expect((await screen.findAllByText(p.code)).length).toBeGreaterThan(0)  // Manual / Shopify: code = name
    expect(screen.getAllByRole('row')).toHaveLength(DEV2_PARTNERS.length + 1)
    const walmart = DEV2_PARTNERS.find((p) => p.code === 'Walmart')!
    expect(walmart.updated_at).toBeNull()
    fireEvent.click(screen.getAllByText('Walmart')[0])
    const sheet = await screen.findByRole('dialog')
    expect(within(sheet).getByLabelText(/^Name/)).toHaveValue('Walmart')
    expect(within(sheet).getByLabelText(/^Channel/)).toHaveTextContent('TRUECOMMERCE')
  })
})
