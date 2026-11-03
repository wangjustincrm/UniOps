import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider, type ListMeta, type QuerySpec } from '@/platform-ui'
import type { LotRead } from './lotsApi'

const row: LotRead = {
  id: 1, lot: 'LOT-A', item_code: 'CF0092', expiry: '2028-05-19', days_to_expiry: 560, qty_shipped_ea: 40, sscc: '00077777770077782891',
  tracking: '1Z-TRACK', shipment_id: 7, document_id: 40, ship_date: '2026-11-02', bol: 'BOL-778', our_reference: 'SO-00042',
  applied: true, shadow: false, order_id: 4, order_ref: 'LCL-4887314555', partner_code: 'AC-9898', partner_name: 'Loblaws',
  ship_to_city: 'Ajax', ship_to_postal: 'L1Z 1X3',
}
const meta: ListMeta = { version: 1, searchable: true, default_sort: ['-ship_date'], views: [], fields: [
  { name: 'lot', label: 'Lot', type: 'text', enum_values: [], filterable: true, sortable: true, groupable: true, operators: ['eq', 'in'] },
  { name: 'qty_shipped_ea', label: 'Qty EA', type: 'number', enum_values: [], filterable: true, sortable: true, groupable: false, operators: ['eq'], summable: true },
] }
const api = vi.hoisted(() => ({ list: vi.fn(), download: vi.fn() }))
vi.mock('./lotsApi', async (orig) => {
  const real = await orig<typeof import('./lotsApi')>()
  return { ...real, lotsClient: { ...real.lotsClient, list: api.list, meta: async () => meta }, exportLots: api.download }
})
const { default: LotsPage } = await import('./LotsPage')
const { parseLots } = await import('./lotsApi')

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={() => true}><LotsPage /></PermissionProvider></ToastProvider></QueryClientProvider>)
}
const lotFilter = (spec: QuerySpec) => (spec.filters ?? []).find((f) => f.field === 'lot')

describe('Lots: tracing and recall', () => {
  beforeEach(() => {
    api.list.mockReset()
    api.download.mockReset()
    api.download.mockResolvedValue(undefined)
    api.list.mockImplementation(async (spec: QuerySpec) => spec.group?.length
      ? { items: [row], total: 3, groups: [{ key: ['Loblaws', 'LCL-4887314555'], count: 2 }, { key: ['Loblaws', 'LCL-4887314746'], count: 1 }] }
      : { items: [row], total: 1, totals: [{ currency: null, count: 1, sums: { qty_shipped_ea: '40' } }] })
  })

  it('each row names the lot, expiry, order, customer, ship-to and tracking; the footer sums the quantity', async () => {
    renderPage()
    const tr = (await screen.findByText('LOT-A')).closest('tr')!
    for (const t of ['CF0092', 'LCL-4887314555', 'Loblaws', 'Ajax · L1Z 1X3', '1Z-TRACK', '00077777770077782891', '#40']) expect(tr).toHaveTextContent(t)
    expect(tr).not.toHaveTextContent('AC-9898')  // the partner code is in the name's tooltip
    expect(tr.closest('table')!.querySelector('tfoot')).toHaveTextContent('40')
  })

  it('a recall takes a pasted list of lots, lists only them, says how far they went, and exports them', async () => {
    renderPage()
    await screen.findByText('LOT-A')
    fireEvent.click(screen.getByRole('button', { name: 'Recall…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Recall: find where lots went' })
    fireEvent.change(within(dialog).getByLabelText('Lots'), { target: { value: 'LOT-A\nLOT-B, LOT-A' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Find 2 lots' }))
    const banner = await screen.findByRole('status', { name: 'Recall' })
    expect(banner).toHaveTextContent('Recall: LOT-A, LOT-B')
    await waitFor(() => expect(banner).toHaveTextContent('3 rows · 2 orders · 1 customer'))
    await waitFor(() => expect(lotFilter(api.list.mock.calls.at(-1)![0])).toEqual({ field: 'lot', op: 'in', value: ['LOT-A', 'LOT-B'] }))
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))
    await waitFor(() => expect(api.download).toHaveBeenCalled())
    expect(lotFilter(api.download.mock.calls[0][0])).toEqual({ field: 'lot', op: 'in', value: ['LOT-A', 'LOT-B'] })
    fireEvent.click(screen.getByRole('button', { name: 'Clear recall' }))
    await waitFor(() => expect(screen.queryByRole('status', { name: 'Recall' })).toBeNull())
  })

  it('pasted lots split on lines, commas, semicolons and spaces, each once', () => {
    expect(parseLots(' LOT-A\nLOT-B;LOT-C , LOT-A\t\n')).toEqual(['LOT-A', 'LOT-B', 'LOT-C'])
  })
})
