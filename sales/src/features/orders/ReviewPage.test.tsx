import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { OrderDetail, OrderRead } from './ordersApi'

const order: OrderRead = {
  id: 4, order_number: 104, partner_code: 'SHOP', source_type: 'SHOPIFY', source_order_id: '1', source_order_number: '9302',
  source_reference: 'SHOP-9302', order_date: '2026-10-29', requested_delivery_date: null, ship_to: { name: 'A B', city: 'Montréal' },
  currency: 'CAD', total: '100.00', fulfillment_status: 'Review', invoice_status: 'Not Required', order_state: 'On Hold',
  hold_reason: null, review_reason: 'line 5: item not in the Item Master (sku=NOPE; 1 CASE)', historical: false,
  first_received_at: '2026-10-29T12:00:00Z', paid_at: '2026-10-29T12:00:00Z', archived_in_shopify_at: null,
  archive_suppressed: false, shopify_financial_status: 'paid', source_updated_at: null, version: 1,
  created_at: '2026-10-29T12:00:00Z', updated_at: null,
}
const detail: OrderDetail = { ...order, notes: null, documents: [{ id: 9, kind: 'SHOPIFY_ORDER_JSON', remote_name: 'orders page 1', created_at: order.created_at }],
  timeline: [], lines: [{ id: 1, line_no: 5, item_code: null, source: { sku: 'NOPE' }, ordered_qty: '1', order_unit: 'CASE', factor_ea: null,
    qty_ea: null, unit_price: '1', line_status: 'Active', removed: false, problem: 'item not in the Item Master' }] }

const api = vi.hoisted(() => ({ resolve: vi.fn() }))
vi.mock('./ordersApi', async (orig) => {
  const real = await orig<typeof import('./ordersApi')>()
  return { ...real, ordersClient: { ...real.ordersClient, list: vi.fn(async () => ({ items: [order], total: 1 })) },
    fetchOrder: vi.fn(async () => detail), resolveReview: api.resolve }
})
const { default: ReviewPage } = await import('./ReviewPage')

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => keys.includes(k)}><ReviewPage /></PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Review worklist', () => {
  it('shows why the order is held and its unmatched line; Re-check needs sales.order.operate', async () => {
    const { unmount } = renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('9302'))
    let dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/item not in the Item Master \(sku=NOPE/)).toBeInTheDocument()
    expect(within(dialog).getByText('unmatched')).toBeInTheDocument()
    expect(within(dialog).queryByText('Re-check')).not.toBeInTheDocument()
    unmount()
    api.resolve.mockResolvedValue({ order, remaining: ['line 5: item not in the Item Master'] })
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByText('9302'))
    dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByText('Re-check'))
    await waitFor(() => expect(api.resolve).toHaveBeenCalledWith(4))
    expect(await within(dialog).findByText('line 5: item not in the Item Master')).toBeInTheDocument()
  })
})
