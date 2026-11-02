import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  PermissionProvider, ToastProvider, ViewsProvider, requestView, type ListMeta, type SavedView, type ViewsClient,
} from '@/platform-ui'
import type { OrderDetail, OrderRead } from './ordersApi'

// Orders with its views (the old Review page is the "Needs review" view) and
// Re-check on Review orders, in the list and in the detail.

const order: OrderRead = {
  id: 4, order_number: 104, partner_code: 'SHOP', source_type: 'SHOPIFY', source_order_id: '1', source_order_number: '9302',
  source_reference: 'SHOP-9302', order_date: '2026-10-29', requested_delivery_date: null, ship_to: { name: 'A B', city: 'Montréal' },
  currency: 'CAD', total: '100.00', fulfillment_status: 'Review', invoice_status: 'Not Required', order_state: 'On Hold',
  hold_reason: null, review_reason: 'line 5: item not in the Item Master (sku=NOPE; 1 CASE)', historical: false,
  first_received_at: '2026-10-29T12:00:00Z', paid_at: '2026-10-29T12:00:00Z', archived_in_shopify_at: null,
  archive_suppressed: false, shopify_financial_status: 'paid', source_updated_at: null, version: 1,
  created_at: '2026-10-29T12:00:00Z', updated_at: null,
}
const pending: OrderRead = { ...order, id: 5, order_number: 105, source_order_number: '9303', fulfillment_status: 'Pending',
  order_state: 'Active', review_reason: null }
const detail: OrderDetail = { ...order, notes: null, shipments: [], documents: [{ id: 9, kind: 'SHOPIFY_ORDER_JSON', direction: 'IN', remote_name: 'orders page 1', created_at: order.created_at,
    file_status: null, ack_status: null, st02: null, sent_at: null, error: null, ack_detail: null }],
  timeline: [], lines: [{ id: 1, line_no: 5, item_code: null, source: { sku: 'NOPE' }, ordered_qty: '1', order_unit: 'CASE', factor_ea: null,
    qty_ea: null, unit_price: '1', line_status: 'Active', removed: false, problem: 'item not in the Item Master' }] }
const meta: ListMeta = {
  version: 1, searchable: true, default_sort: ['-order_number'],
  fields: [
    { name: 'order_number', label: 'Order', type: 'number', enum_values: [], filterable: true, sortable: true, groupable: false, operators: ['eq'] },
    { name: 'fulfillment_status', label: 'Status', type: 'enum', enum_values: ['Review', 'Pending'], filterable: true, sortable: true, groupable: true, operators: ['eq', 'in'] },
  ],
  views: [
    { key: 'all', name: 'All', state: { version: 1, hidden: ['review_reason'] } },
    { key: 'needs_review', name: 'Needs review', state: { version: 1, filters: [{ field: 'fulfillment_status', op: 'eq', value: 'Review' }] } },
  ],
}

const api = vi.hoisted(() => ({ resolve: vi.fn(), fetchOrder: vi.fn(), list: vi.fn(), send: vi.fn(), eligible: vi.fn() }))
vi.mock('./ordersApi', async (orig) => {
  const real = await orig<typeof import('./ordersApi')>()
  return { ...real, ordersClient: { ...real.ordersClient, list: api.list, meta: async () => meta },
    fetchOrder: api.fetchOrder, resolveReview: api.resolve, send940: api.send, fetchEligibility: api.eligible }
})
const { default: OrdersPage } = await import('./OrdersPage')

function fakeViews(mine: SavedView[] = []): ViewsClient & { rows: SavedView[] } {
  const rows = [...mine]
  return {
    rows,
    list: vi.fn(async () => [...rows]),
    create: vi.fn(async (resource, name, state) => { const v = { id: rows.length + 10, resource, name, state, is_default: false, sort: 0, version: 1, stale: false }; rows.push(v); return v }),
    update: vi.fn(async (id, data) => { const v = rows.find((r) => r.id === id)!; Object.assign(v, data, { version: v.version + 1 }); return v }),
    remove: vi.fn(async (id) => { rows.splice(rows.findIndex((r) => r.id === id), 1) }),
    setDefault: vi.fn(async (id, on) => { rows.forEach((r) => { r.is_default = on && r.id === id }); return rows.find((r) => r.id === id)! }),
  }
}

function renderAs(keys: string[], views: ViewsClient = fakeViews()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><ViewsProvider client={views}>
    <PermissionProvider can={(k) => keys.includes(k)}><OrdersPage /></PermissionProvider></ViewsProvider></ToastProvider></QueryClientProvider>)
}
const lastFilters = () => (api.list.mock.calls.at(-1)![0].filters ?? [])

describe('Orders: Re-check on Review orders', () => {
  beforeEach(() => {
    api.fetchOrder.mockReset(); api.fetchOrder.mockResolvedValue(detail)
    api.list.mockReset(); api.list.mockImplementation(async () => ({ items: [order, pending], total: 2 }))
  })

  it('in the detail: why it is held; Re-check needs sales.order.operate and says that it ran', async () => {
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
    expect(within(dialog).getByRole('status')).toHaveTextContent(/Re-checked at .*: still 1 problem/)
  })

  it('a re-check shows the new reason and lines at once, without reopening or reloading', async () => {
    const fixed = { ...detail, review_reason: 'line 5: the variant title looks like a pack size this item has no unit for',
      lines: [{ ...detail.lines[0], item_code: 'CF0092', problem: 'the variant title looks like a pack size this item has no unit for' }] }
    api.resolve.mockImplementation(async () => {
      api.fetchOrder.mockResolvedValue(fixed)  // the server's state after the re-check
      return { order, remaining: [fixed.review_reason] }
    })
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByText('9302'))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/item not in the Item Master \(sku=NOPE/)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByText('Re-check'))
    await waitFor(() => expect(within(dialog).queryByText(/item not in the Item Master \(sku=NOPE/)).not.toBeInTheDocument())
    expect(within(dialog).getByText('CF0092')).toBeInTheDocument()
  })

  it('in the list: a row action on Review orders only, which does not open the order', async () => {
    api.resolve.mockResolvedValue({ order, remaining: ['line 5: x'] })
    renderAs(['sales.read', 'sales.order.operate'])
    const reviewRow = (await screen.findByText('9302')).closest('tr')!
    const pendingRow = screen.getByText('9303').closest('tr')!
    expect(within(pendingRow).queryByText('Re-check')).toBeNull()
    fireEvent.click(within(reviewRow).getByText('Re-check'))
    await waitFor(() => expect(api.resolve).toHaveBeenCalledWith(4))
    expect(await within(reviewRow).findByRole('status')).toHaveTextContent(/still 1 problem/)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('Orders: views', () => {
  beforeEach(() => { api.list.mockReset(); api.list.mockImplementation(async () => ({ items: [order], total: 1 })) })

  it('opens with the first system view; Needs review is the old Review list', async () => {
    renderAs(['sales.read'])
    const picker = await screen.findByRole('button', { name: 'View: All' })
    expect(api.list).toHaveBeenCalledTimes(1)  // waits for the view: one request, not two
    expect(lastFilters()).toEqual([])
    expect(screen.queryByRole('columnheader', { name: /Review reason/ })).toBeNull()  // All hides it
    fireEvent.click(picker)
    fireEvent.click(within(await screen.findByRole('group', { name: 'System views' })).getByText('Needs review'))
    await waitFor(() => expect(lastFilters()).toEqual([{ field: 'fulfillment_status', op: 'eq', value: 'Review' }]))
    expect(screen.getByRole('columnheader', { name: /Review reason/ })).toBeInTheDocument()
  })

  it('a change marks the view unsaved; Reset goes back; Save as new keeps it as mine', async () => {
    const views = fakeViews()
    renderAs(['sales.read'], views)
    await screen.findByRole('button', { name: 'View: All' })
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: '9302' } })
    fireEvent.keyDown(screen.getByLabelText('Search'), { key: 'Enter' })
    expect(await screen.findByRole('button', { name: 'View: All (unsaved changes)' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    await waitFor(() => expect(api.list.mock.calls.at(-1)![0].search).toBeUndefined())
    expect(screen.getByRole('button', { name: 'View: All' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: '9302' } })
    fireEvent.keyDown(screen.getByLabelText('Search'), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('button', { name: 'Save' }))  // a system view: Save = Save as new
    const dialog = await screen.findByRole('dialog', { name: 'Save as a new view' })
    fireEvent.change(within(dialog).getByLabelText('View name'), { target: { value: 'Mine 9302' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(views.create).toHaveBeenCalledWith('orders', 'Mine 9302', expect.objectContaining({ search: '9302', version: 1 })))
    expect(await screen.findByRole('button', { name: 'View: Mine 9302' })).toBeInTheDocument()
  })

  it('my default view opens the list; a requested system view wins once', async () => {
    const views = fakeViews([{ id: 7, resource: 'orders', name: 'Big', is_default: true, sort: 0, version: 1, stale: false,
      state: { version: 1, sort: [], group: [], filters: [{ field: 'order_number', op: 'eq', value: '104' }], pageSize: 25, columns: [], hidden: [] } }])
    const { unmount } = renderAs(['sales.read'], views)
    expect(await screen.findByRole('button', { name: 'View: Big' })).toBeInTheDocument()
    expect(lastFilters()).toEqual([{ field: 'order_number', op: 'eq', value: '104' }])
    unmount()
    requestView('orders', 'needs_review')  // e.g. the old /review address
    renderAs(['sales.read'], views)
    expect(await screen.findByRole('button', { name: 'View: Needs review' })).toBeInTheDocument()
  })
})

describe('Orders: 940s (P4)', () => {
  const sentOrder: OrderRead = { ...pending, id: 6, order_number: 106, source_order_number: '9304', fulfillment_status: '940 Sent' }
  const out = (id: number, file_status: string, ack_status: string, st02: string) => ({ id, kind: '940_EDI', direction: 'OUT' as const,
    remote_name: `940-SHOP-${st02}.edi`, created_at: order.created_at, file_status, ack_status, st02, sent_at: '2026-10-30T14:00:00Z',
    error: null, ack_detail: null })
  beforeEach(() => {
    api.send.mockReset(); api.eligible.mockReset()
    api.list.mockReset(); api.list.mockImplementation(async () => ({ items: [pending, sentOrder], total: 2 }))
  })

  it('Send 940 now needs sales.order.operate and says the 940 is queued', async () => {
    api.fetchOrder.mockResolvedValue({ ...detail, ...pending, documents: [] })
    const { unmount } = renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('9303'))
    let dialog = await screen.findByRole('dialog')
    await within(dialog).findByText('Timeline')
    expect(within(dialog).queryByText('Send 940 now')).toBeNull()
    unmount()
    api.send.mockResolvedValue({ order: pending, document_id: 20, file_status: 'Ready To Send', control: '000000123', errors: [] })
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByText('9303'))
    dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByText('Send 940 now'))
    await waitFor(() => expect(api.send).toHaveBeenCalledWith(5, false))
    expect(await within(dialog).findByRole('status')).toHaveTextContent('940 000000123 queued')
  })

  it('a sent 940 shows Sent and the 997 apart; a change goes only through a confirmed replacement', async () => {
    api.fetchOrder.mockResolvedValue({ ...detail, ...sentOrder, documents: [out(30, 'Sent', 'Waiting', '000000122')] })
    api.send.mockResolvedValue({ order: sentOrder, document_id: 31, file_status: 'Ready To Send', control: '000000124', errors: [] })
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByText('9304'))
    const dialog = await screen.findByRole('dialog', { name: 'Order 106' })
    const table = await within(dialog).findByRole('region', { name: 'Warehouse 940s' })
    const row = within(table).getByText('000000122').closest('tr')!
    expect(within(row).getByText('Sent')).toBeInTheDocument()
    expect(within(row).getByText('Waiting')).toBeInTheDocument()  // sent, not accepted
    expect(within(dialog).queryByText('Send 940 now')).toBeNull()
    fireEvent.click(within(dialog).getByText('Replace 940'))
    const confirm = await screen.findByRole('dialog', { name: 'Replace the live 940?' })
    expect(api.send).not.toHaveBeenCalled()
    fireEvent.click(within(confirm).getByRole('button', { name: 'Replace 940' }))
    await waitFor(() => expect(api.send).toHaveBeenCalledWith(6, true))
  })

  it('a 940 that fails its checks says why and that it was not sent', async () => {
    api.fetchOrder.mockResolvedValue({ ...detail, ...pending, documents: [] })
    api.send.mockResolvedValue({ order: pending, document_id: 21, file_status: 'Cancelled', control: '000000125',
      errors: ['ship_to.zip is empty'] })
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByText('9303'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByText('Send 940 now'))
    expect(await within(dialog).findByRole('status')).toHaveTextContent(/failed its checks and was not sent.*ship_to.zip is empty/)
  })

  it('Next 940s lists what the next cycle would send, writing nothing', async () => {
    api.eligible.mockResolvedValue([{ order_id: 5, order_number: 105, partner_code: 'SHOP', source_order_number: '9303' }])
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByRole('button', { name: 'Next 940s' }))
    const dialog = await screen.findByRole('dialog', { name: 'Next 940s' })
    expect(await within(dialog).findByText('Order 105 · SHOP 9303')).toBeInTheDocument()
    expect(api.send).not.toHaveBeenCalled()
  })
})
