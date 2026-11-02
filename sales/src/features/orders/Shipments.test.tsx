import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { ShipmentRead } from './ordersApi'

const api = vi.hoisted(() => ({ retry: vi.fn() }))
vi.mock('./ordersApi', async (orig) => {
  const real = await orig<typeof import('./ordersApi')>()
  return { ...real, retryShopify: api.retry }
})
const { Shipments } = await import('./Shipments')

const base: ShipmentRead = {
  id: 7, document_id: 40, order_key_received: '9301', ship_date: '2026-11-02', ship_date_source: 'DTM', carrier: 'WIFS',
  mode: 'LT', tracking: ['1Z-TRACK'], bol: null, applied: true, shadow: false,
  checks: [{ check: 'over-shipment', message: 'CF0092: shipped 14 EA against 12 on the 940' }],
  shopify_status: 'Failed', shopify_error: 'archiving the order: requires write_orders',
  rows: [
    { lx_index: 1, w12_index: 1, sscc: '00077777770077782891', item_code: 'CF0092', lot: 'LOT-A', expiry: '2028-05-19', qty_ordered_ea: 12, qty_shipped_ea: 10, tracking: null, needs_review: false },
    { lx_index: 1, w12_index: 2, sscc: '00077777770077782891', item_code: 'CF0010', lot: 'LOT-B', expiry: null, qty_ordered_ea: 1, qty_shipped_ea: 1, tracking: null, needs_review: true },
  ],
}

function renderAs(keys: string[], shipments: ShipmentRead[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider>
    <PermissionProvider can={(k) => keys.includes(k)}><Shipments shipments={shipments} orderId={3} /></PermissionProvider>
  </ToastProvider></QueryClientProvider>)
}

describe('Order shipments (945)', () => {
  it('shows every pallet row with its lot, the checks, the row to review and the Shopify failure with Retry', async () => {
    api.retry.mockResolvedValue({ shipment_id: 7, shopify_status: 'Pending' })
    renderAs(['sales.read', 'sales.order.operate'], [base])
    const group = screen.getByRole('group', { name: 'Shipment 7' })
    expect(within(group).getAllByText('00077777770077782891')).toHaveLength(2)  // leading zeros kept
    expect(within(group).getByText('LOT-B').closest('tr')).toHaveTextContent('matches no order line')
    expect(within(group).getByRole('list', { name: '945 checks' })).toHaveTextContent('over-shipment: CF0092: shipped 14 EA')
    expect(within(group).getByText('archiving the order: requires write_orders')).toBeInTheDocument()
    fireEvent.click(within(group).getByRole('button', { name: 'Retry in Shopify' }))
    await waitFor(() => expect(api.retry).toHaveBeenCalledWith(7))
  })

  it('a shadow 945 says it was not applied and offers no retry', () => {
    renderAs(['sales.read', 'sales.order.operate'], [{ ...base, shadow: true, applied: false, shopify_status: 'Suppressed', shopify_error: null }])
    expect(screen.getByText('Shadow run: not applied')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry in Shopify' })).toBeNull()
  })
})
