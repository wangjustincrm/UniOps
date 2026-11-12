import { noticeIn } from '@/platform-ui/testing'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider, ViewsProvider } from '@/platform-ui'
import { type ListMeta } from '@/platform-ui/resource/listMeta'
import { type ViewsClient } from '@/platform-ui/resource/viewsApi'
import type { OrderDetail, OrderRead } from '@/features/orders/ordersApi'
import type { InvoiceDetail, InvoiceRead } from './invoicesApi'

const order: OrderRead = {
  id: 7, order_number: 107, partner_code: 'LBL', source_type: 'EDI_850', source_order_id: 'PO-1', source_order_number: 'PO-1',
  source_reference: 'LBL-PO-1', partner_name: 'Loblaws', order_date: '2026-10-28', requested_delivery_date: '2026-11-05', ship_to: null, currency: 'CAD', total: '500.00',
  fulfillment_status: 'Shipped', invoice_status: 'Waiting for Delivery Confirmation', order_state: 'Active', hold_reason: null,
  order_status: 'Shipped', on_hold_reason: null, order_status_label: 'Shipped', short_shipped: false, status_detail: null, status_detail_at: null, status_detail_kind: null,
  shadow: false, shopify_fulfillment_status: null, source_cancelled_at: null,
  review_reason: null, historical: false, first_received_at: '2026-10-28T12:00:00Z', paid_at: null, archived_in_shopify_at: null,
  archive_suppressed: false, shopify_financial_status: null, source_updated_at: null, version: 1, created_at: '2026-10-28T12:00:00Z', updated_at: null,
}
const detail: OrderDetail = { ...order, bill_to: null, dc_number: null, notes: null, documents: [], shipments: [], timeline: [], lines: [
  { id: 70, line_no: 1, item_code: 'CF0092', source: {}, ordered_qty: '10', order_unit: 'CASE', factor_ea: '6', qty_ea: 60, unit_price: '50',
    line_status: 'Active', removed: false, problem: null, shipped_ea: 60, delivered_qty: null }] }
const inv: InvoiceRead = {
  id: 3, invoice_number: 'IN-107', order_id: 7, method: '810_JSON', invoice_date: '2026-11-05', currency: 'CAD', payment_terms: 'Net 30',
  subtotal: '450.00', total: '450.00', document_id: null, sent_at: null, send_count: 0, error: null, version: 1, created_at: '2026-11-05T12:00:00Z',
  updated_by_name: null, order_number: 107, partner_code: 'LBL', po: 'PO-1', status: 'Blocked', order_ref: 'LBL-PO-1', partner_name: 'Loblaws', delivery_number: null,
}
const invDetail: InvoiceDetail = { ...inv, confirmed_date: '2026-11-05', lines: [
  { id: 30, line_no: 1, item_code: 'CF0092', buyer_part: 'L-555', description: null, uom: 'CA', qty: 9, unit_price: '50.00000', amount: '450.00',
    po_price: '50.00000' }] }
const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }

const api = vi.hoisted(() => ({ list: vi.fn(), fetchOrder: vi.fn(), bulk: vi.fn(), confirm: vi.fn(), invList: vi.fn(),
  fetchInvoice: vi.fn(), send: vi.fn(), edit: vi.fn(), release: vi.fn() }))
vi.mock('@/features/orders/ordersApi', async (orig) => {
  const real = await orig<typeof import('@/features/orders/ordersApi')>()
  return { ...real, ordersClient: { ...real.ordersClient, list: api.list, meta: async () => meta }, fetchOrder: api.fetchOrder }
})
vi.mock('./invoicesApi', async (orig) => {
  const real = await orig<typeof import('./invoicesApi')>()
  return { ...real, invoicesClient: { ...real.invoicesClient, list: api.invList, meta: async () => meta },
    confirmBulk: api.bulk, confirmDelivery: api.confirm, fetchInvoice: api.fetchInvoice, sendInvoice: api.send,
    editInvoice: api.edit, releaseInvoice: api.release }
})
const { default: DeliveryPage } = await import('./DeliveryPage')
const { default: InvoicesPage } = await import('./InvoicesPage')

const views: ViewsClient = { list: vi.fn(async () => []), create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
function renderAs(keys: string[], page: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><ViewsProvider client={views}>
    <PermissionProvider can={(k) => keys.includes(k)}>{page}</PermissionProvider></ViewsProvider></ToastProvider></QueryClientProvider>)
}

describe('Delivery confirmation', () => {
  beforeEach(() => {
    Object.values(api).forEach((f) => f.mockReset())
    api.list.mockResolvedValue({ items: [order], total: 1 }); api.fetchOrder.mockResolvedValue(detail)
  })

  it('lists only orders waiting for confirmation and confirms the shown ones in bulk', async () => {
    api.bulk.mockResolvedValue([{ order_id: 7, ok: true, invoice_id: 3, status: 'Ready', error: null }])
    renderAs(['sales.read', 'sales.order.operate'], <DeliveryPage />)
    await screen.findByText('PO-1')
    expect(api.list.mock.calls.at(-1)![0].filters).toEqual([{ field: 'to_confirm', op: 'eq', value: 'true' }])
    fireEvent.click(screen.getByRole('button', { name: /Confirm all shown \(1\)/ }))
    const ask = await screen.findByRole('dialog', { name: 'Confirm 1 deliveries' })
    fireEvent.change(within(ask).getByLabelText('Delivered on (optional)'), { target: { value: '2026-11-05' } })
    fireEvent.click(within(ask).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(api.bulk).toHaveBeenCalledWith([7], '2026-11-05', false))
    await waitFor(() => expect(noticeIn()).toHaveTextContent('1 confirmed'))  // the list's status row
  })

  it('one order: the date starts at the Delivery due date, a person may change it, Confirm and send sends', async () => {
    api.list.mockResolvedValue({ items: [{ ...order, delivery_due_date: '2026-11-01' }], total: 1 })
    api.confirm.mockResolvedValue(invDetail)
    renderAs(['sales.read', 'sales.order.operate', 'sales.invoice.manage'], <DeliveryPage />)
    fireEvent.click(await screen.findByText('PO-1'))
    const form = await screen.findByRole('dialog')
    await within(form).findByRole('table', { name: 'Returned and delivered' })
    expect(within(form).getByLabelText(/Delivered on \(the invoice date\)/)).toHaveValue('2026-11-01')
    fireEvent.click(within(form).getByRole('button', { name: 'Confirm and send' }))
    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith(expect.objectContaining({ order_id: 7, confirmed_date: '2026-11-01', send: true })))
  })

  it('one order: what the customer returned per line; delivered = shipped − returned is what is sent', async () => {
    api.confirm.mockResolvedValue(invDetail)
    renderAs(['sales.read', 'sales.order.operate'], <DeliveryPage />)
    fireEvent.click(await screen.findByText('PO-1'))
    const dialog = await screen.findByRole('dialog')
    const form = dialog
    await within(form).findByRole('table', { name: 'Returned and delivered' })
    fireEvent.change(within(form).getByLabelText('Returned CF0092'), { target: { value: '6' } })  // 60 shipped: 54 delivered
    fireEvent.change(within(form).getByLabelText(/Delivered on \(the invoice date\)/), { target: { value: '2026-11-05' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Confirm delivery' }))
    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith({ order_id: 7, confirmed_date: '2026-11-05',
      lines: [{ order_line_id: 70, delivered_qty: 54 }] }))
  })

  it('with no date each order takes its due date, and Confirm and send needs invoice.manage too', async () => {
    api.bulk.mockResolvedValue([{ order_id: 7, ok: true, invoice_id: 3, status: 'Queued', sent: true, error: null }])
    renderAs(['sales.read', 'sales.order.operate', 'sales.invoice.manage'], <DeliveryPage />)
    await screen.findByText('PO-1')
    fireEvent.click(screen.getByRole('button', { name: /Confirm all shown \(1\)/ }))
    const ask = await screen.findByRole('dialog', { name: 'Confirm 1 deliveries' })
    fireEvent.click(within(ask).getByRole('button', { name: 'Confirm and send' }))
    await waitFor(() => expect(api.bulk).toHaveBeenCalledWith([7], null, true))
    await waitFor(() => expect(noticeIn()).toHaveTextContent('1 confirmed, 1 sent'))
  })

  it('without invoice.manage only Confirm is offered', async () => {
    renderAs(['sales.read', 'sales.order.operate'], <DeliveryPage />)
    await screen.findByText('PO-1')
    fireEvent.click(screen.getByRole('button', { name: /Confirm all shown \(1\)/ }))
    const ask = await screen.findByRole('dialog', { name: 'Confirm 1 deliveries' })
    expect(within(ask).queryByRole('button', { name: 'Confirm and send' })).toBeNull()
  })

  it('invoice.manage alone does not confirm delivery: it is order work', async () => {
    renderAs(['sales.read', 'sales.invoice.manage'], <DeliveryPage />)
    await screen.findByText('PO-1')
    expect(screen.queryByRole('button', { name: /Confirm all shown/ })).toBeNull()
  })

  it('a reader cannot confirm', async () => {
    renderAs(['sales.read'], <DeliveryPage />)
    await screen.findByText('PO-1')
    expect(screen.queryByRole('button', { name: /Confirm all shown/ })).toBeNull()
  })
})

describe('Invoices', () => {
  beforeEach(() => {
    Object.values(api).forEach((f) => f.mockReset())
    api.invList.mockResolvedValue({ items: [inv], total: 1 }); api.fetchInvoice.mockResolvedValue(invDetail)
  })

  it('a held invoice is released with a reason; it is not sendable while held', async () => {
    api.release.mockResolvedValue({ order_id: 7, invoice_status: 'Ready' })
    renderAs(['sales.read', 'sales.invoice.manage'], <InvoicesPage />)
    fireEvent.click(await screen.findByText('IN-107'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Release' }))
    expect(within(dialog).queryByRole('button', { name: 'Send' })).toBeNull()
    const rel = await screen.findByRole('dialog', { name: 'Release invoice IN-107?' })
    const button = within(rel).getByRole('button', { name: 'Release' })
    expect(button).toBeDisabled()  // a reason first
    fireEvent.change(within(rel).getByLabelText(/Why it can go ahead/), { target: { value: 'customer accepts 9 cases' } })
    fireEvent.click(button)
    await waitFor(() => expect(api.release).toHaveBeenCalledWith(7, 'customer accepts 9 cases'))
  })

  it('a ready invoice is edited, then sent', async () => {
    api.fetchInvoice.mockResolvedValue({ ...invDetail, status: 'Ready' })
    api.edit.mockResolvedValue({ ...invDetail, status: 'Ready', version: 2 })
    api.send.mockResolvedValue({ ...invDetail, status: 'Queued' })
    renderAs(['sales.read', 'sales.invoice.manage'], <InvoicesPage />)
    fireEvent.click(await screen.findByText('IN-107'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Edit invoice' }))
    // the PO price stays beside the invoice price; a different price is marked and needs a reason
    fireEvent.change(await within(dialog).findByLabelText('Unit price line 1'), { target: { value: '48.5' } })
    const lines = within(dialog).getByRole('table', { name: 'Invoice lines' })
    expect(within(lines).getByText(/vs PO/)).toHaveTextContent('-1.50 CAD vs PO')
    expect(within(dialog).getByRole('button', { name: 'Save changes' })).toBeDisabled()
    expect(within(dialog).getByText('An invoice price changed: give the reason for the price change.')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText(/^Delivery number/), { target: { value: 'POD-4471' } })
    fireEvent.change(within(dialog).getByLabelText(/^Reason for a price change/), { target: { value: 'agreed promo' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.edit).toHaveBeenCalledWith(3, 1, { invoice_date: '2026-11-05', delivery_number: 'POD-4471',
      reason: 'agreed promo', lines: [{ id: 30, qty: 9, unit_price: '48.5' }] }))
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Send' }))
    await waitFor(() => expect(api.send).toHaveBeenCalledWith(3))
  })
})
