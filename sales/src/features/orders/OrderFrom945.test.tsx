import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { OrderFrom945Plan } from './ordersApi'

const plan: OrderFrom945Plan = {
  shipment_id: 45, document_id: 79, order_key: '001008561', reference: 'CRM-940_001008561_Walmart_4550827924', partner_code: 'Walmart',
  partner_name_received: 'Walmart', po: '4550827924', ship_date: '2026-11-04', ship_to: { name: 'Store', city: 'Toronto' },
  lines: [{ item_code: 'S0156', received: 'S0156', qty_ea: 1320, shipped_ea: 1320, problem: null },
    { item_code: null, received: 'NOPE', qty_ea: 5, shipped_ea: 5, problem: 'item not in the Item Master' }],
  order_id: null, order_ref: null, refused: null, refused_message: null,
}
const fetchPlans = vi.fn<(id: number) => Promise<OrderFrom945Plan[]>>()
const create = vi.fn(async () => ({ id: 9, order_number: 9, source_reference: 'WM-4550827924' }))
vi.mock('./ordersApi', async (orig) => ({
  ...(await orig<typeof import('./ordersApi')>()), fetchShipmentsWithoutOrder: (id: number) => fetchPlans(id), createOrderFrom945: (...a: unknown[]) => (create as (...x: unknown[]) => unknown)(...a),
}))
const { OrderFrom945 } = await import('./OrderFrom945')

function show(can: (k: string) => boolean = () => true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<MemoryRouter><QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={can}>
    <OrderFrom945 documentId={79} /></PermissionProvider></ToastProvider></QueryClientProvider></MemoryRouter>)
}

describe('Order from a 945', () => {
  beforeEach(() => { create.mockClear() })

  it('shows what shipped, keeps an unknown item with its note, and creates with the partner from the 945', async () => {
    fetchPlans.mockResolvedValue([plan])
    show()
    expect((await screen.findAllByText('1320')).length).toBe(2)
    expect(screen.getByText('item not in the Item Master')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create order' }))
    await waitFor(() => expect(create).toHaveBeenCalledWith(45, 'Walmart'))
  })

  it('waits for a partner when the 945 names none', async () => {
    fetchPlans.mockResolvedValue([{ ...plan, partner_code: null, partner_name_received: null, po: null, reference: null }])
    show()
    expect(await screen.findByRole('button', { name: 'Create order' })).toBeDisabled()
  })

  it('offers to join the order a later part belongs to', async () => {
    fetchPlans.mockResolvedValue([{ ...plan, order_id: 3, order_ref: 'WM-1' }])
    show()
    expect(await screen.findByRole('button', { name: 'Add to the order' })).toBeEnabled()
    expect(screen.getByText(/another part of order WM-1/)).toBeInTheDocument()
  })

  it('says why when no order can be made, with no button', async () => {
    fetchPlans.mockResolvedValue([{ ...plan, refused: 'order945.outside_shadow', refused_message: 'only during the shadow run' }])
    show()
    expect(await screen.findByText('only during the shadow run')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create order' })).not.toBeInTheDocument()
  })
})
