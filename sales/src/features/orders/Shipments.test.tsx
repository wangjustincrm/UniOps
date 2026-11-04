import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { OrderDetail, ShipmentRead } from './ordersApi'

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
    { id: 1, lx_index: 1, w12_index: 1, sscc: '00077777770077782891', item_code: 'CF0092', lot: 'LOT-A', expiry: '2028-05-19', qty_ordered_ea: 12, qty_shipped_ea: 10, tracking: null, needs_review: false },
    { id: 2, lx_index: 1, w12_index: 2, sscc: '00077777770077782891', item_code: 'CF0010', lot: 'LOT-B', expiry: null, qty_ordered_ea: 1, qty_shipped_ea: 1, tracking: null, needs_review: true },
  ],
}

function renderAs(keys: string[], shipments: ShipmentRead[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<MemoryRouter><QueryClientProvider client={qc}><ToastProvider>
    <PermissionProvider can={(k) => keys.includes(k)}><Shipments shipments={shipments} orderId={3} /></PermissionProvider>
  </ToastProvider></QueryClientProvider></MemoryRouter>)
}

describe('Order shipments (945)', () => {
  it('sums its rows, lists the row matching no line, the checks and the Shopify failure with Retry', async () => {
    api.retry.mockResolvedValue({ shipment_id: 7, shopify_status: 'Pending' })
    renderAs(['sales.read', 'sales.order.operate'], [base])
    const group = screen.getByRole('group', { name: 'Shipment 7' })
    expect(group).toHaveTextContent('2 rows · 11 EA')
    expect(within(group).getByText('LOT-B').closest('tr')).toHaveTextContent('matches no order line')
    expect(within(group).getByText('LOT-A').closest('tr')).toHaveTextContent('Not linked')
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

  it('the card says where it went, by whom (in words), its control numbers, its file, and no 997 back', () => {
    renderAs(['sales.read'], [{ ...base, shopify_status: 'Done', shopify_error: null, checks: null, carrier: 'PRLA', carrier_name: 'Purolator', mode: 'U',
      bol: 'BOL-1', our_reference: '9301', isa13: '000000781', gs06: '781', set_control: '0001', received_at: '2026-11-02T14:05:00Z',
      document_name: 'CRM-945_X.edi', ship_to: { name: 'Store 12', city: 'Toronto', province: 'ON', zip: 'M5V 1A1' },
      tracking_links: [{ number: '1Z-TRACK', url: 'https://t.example/?pin=1Z-TRACK' }] }])
    const group = screen.getByRole('group', { name: 'Shipment 7' })
    for (const text of ['Store 12', 'Toronto ON M5V 1A1', 'Purolator', '(PRLA)', 'Parcel', 'BOL-1', '000000781 / 781 / 0001', 'CRM-945_X.edi'])
      expect(group).toHaveTextContent(text)
    expect(group).not.toHaveTextContent('997')  // never one for a 945: no field at all
    expect(within(group).getByRole('button', { name: 'CRM-945_X.edi' })).toBeInTheDocument()  // opens it on Documents
  })

  it('the line items: a table per pallet with its subtotal, each row with its line, cases and match', () => {
    const rows: ShipmentRead['rows'] = [
      { ...base.rows[0], lx_number: '1', line_no: 11, order_line_id: 5, description: 'Milk powder', case_factor: 6, qty_shipped_ea: 12, needs_review: false, upc: '062639300012' },
      { ...base.rows[0], w12_index: 2, lot: 'LOT-C', lx_number: '1', line_no: 12, order_line_id: 6, item_code: 'CF0010', qty_shipped_ea: 5, needs_review: false },
      { ...base.rows[0], lx_index: 2, w12_index: 1, sscc: '00077777770077782907', lot: 'LOT-D', lx_number: '2', line_no: 11, order_line_id: 5, qty_shipped_ea: 3, needs_review: false },
    ]
    renderAs(['sales.read'], [{ ...base, shadow: true, applied: false, shopify_status: 'Suppressed', shopify_error: null, checks: null, rows, part: 'a' }])
    expect(screen.getByText('Part a')).toBeInTheDocument()
    const p1 = screen.getByRole('table', { name: 'Shipment 7 pallet 1 line items' })
    const p2 = screen.getByRole('table', { name: 'Shipment 7 pallet 2 line items' })
    expect(within(p1).getAllByRole('row').filter((r) => r.closest('tbody'))).toHaveLength(2)
    expect(within(p1).getByText('LOT-A').closest('tr')).toHaveTextContent(/#11 · CF0092.*Milk powder.*12 \(2 CS\).*Matched \(shadow\)/)
    expect(p1.querySelector('tfoot')).toHaveTextContent(/Pallet subtotal\s*17/)
    expect(p2.querySelector('tfoot')).toHaveTextContent(/Pallet subtotal\s*3/)
    expect(screen.getByRole('region', { name: 'Pallet 2' })).toHaveTextContent('SSCC 00077777770077782907 · 3 EA')
  })

  it("a Shopify order's line by its position (the line-item id in a tooltip); an 850's by its PO line number", () => {
    const rows: ShipmentRead['rows'] = [{ ...base.rows[0], line_no: 51697745592626, order_line_id: 5, needs_review: false }]
    const qc = new QueryClient()
    const { unmount } = render(<MemoryRouter><QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={() => true}>
      <Shipments shipments={[{ ...base, checks: null, rows }]} orderId={3} lineNumbers={new Map([[51697745559858, 1], [51697745592626, 2]])} />
    </PermissionProvider></ToastProvider></QueryClientProvider></MemoryRouter>)
    const cell = screen.getByText('LOT-A').closest('tr')!
    expect(cell).toHaveTextContent('#2 · CF0092')
    expect(cell.querySelector('[data-line-id="51697745592626"]')).not.toBeNull()
    unmount()
    renderAs(['sales.read'], [{ ...base, checks: null, rows: [{ ...rows[0], line_no: 11 }] }])
    expect(screen.getByText('LOT-A').closest('tr')).toHaveTextContent('#11 · CF0092')
  })
})

describe('lineNumbers', () => {
  it('Shopify: positions in line_no order; an 850: the PO line number as is', async () => {
    const { lineNumbers } = await import('./ordersApi')
    const lines = [{ line_no: 51697745953074 }, { line_no: 51697745559858 }, { line_no: 51697745592626 }] as OrderDetail['lines']
    expect([...lineNumbers({ source_type: 'SHOPIFY', lines })]).toEqual([[51697745559858, 1], [51697745592626, 2], [51697745953074, 3]])
    expect([...lineNumbers({ source_type: 'EDI_850', lines: [{ line_no: 11 }, { line_no: 3 }] as OrderDetail['lines'] })]).toEqual([[11, 11], [3, 3]])
  })
})
