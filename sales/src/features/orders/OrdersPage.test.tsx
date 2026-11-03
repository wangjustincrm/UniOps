import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  PermissionProvider, ToastProvider, ViewsProvider, requestView, type ListMeta, type SavedView, type ViewsClient,
} from '@/platform-ui'
import { MemoryRouter } from 'react-router-dom'
import { chooseOption, noticeIn, openTab, pickSuggestion } from '@/platform-ui/testing'
import type { OrderDetail, OrderRead } from './ordersApi'

// Orders with its views (the old Review page is the "Needs review" view) and
// Re-check on Review orders, in the list and in the detail.

const order: OrderRead = {
  id: 4, order_number: 104, partner_code: 'SHOP', source_type: 'SHOPIFY', source_order_id: '1', source_order_number: '9302',
  source_reference: 'SHOP-9302', partner_name: 'Shopify', order_date: '2026-10-29', requested_delivery_date: null, ship_to: { name: 'A B', city: 'Montréal' },
  currency: 'CAD', total: '100.00', fulfillment_status: 'Review', invoice_status: 'Not Required', order_state: 'On Hold',
  hold_reason: null, review_reason: 'line 5: item not in the Item Master (sku=NOPE; 1 CASE)', historical: false,
  first_received_at: '2026-10-29T12:00:00Z', paid_at: '2026-10-29T12:00:00Z', archived_in_shopify_at: null,
  archive_suppressed: false, shopify_financial_status: 'paid', source_updated_at: null, version: 1,
  created_at: '2026-10-29T12:00:00Z', updated_at: null,
}
const pending: OrderRead = { ...order, id: 5, order_number: 105, source_order_number: '9303', source_reference: 'SHOP-9303', fulfillment_status: 'Pending',
  order_state: 'Active', review_reason: null }
const detail: OrderDetail = { ...order, bill_to: null, dc_number: null, notes: null, shipments: [], documents: [{ id: 9, kind: 'SHOPIFY_ORDER_JSON', direction: 'IN', remote_name: 'orders page 1', created_at: order.created_at,
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

const api = vi.hoisted(() => ({ resolve: vi.fn(), fetchOrder: vi.fn(), list: vi.fn(), send: vi.fn(), eligible: vi.fn(),
  preview: vi.fn(), create: vi.fn(), submit: vi.fn(), update: vi.fn(), prices: vi.fn() }))
vi.mock('./ordersApi', async (orig) => {
  const real = await orig<typeof import('./ordersApi')>()
  return { ...real, ordersClient: { ...real.ordersClient, list: api.list, meta: async () => meta },
    fetchOrder: api.fetchOrder, resolveReview: api.resolve, send940: api.send, fetchEligibility: api.eligible,
    previewManual: api.preview, createManual: api.create, submitManual: api.submit, updateManual: api.update,
    setInvoicePrices: api.prices }
})
const look = vi.hoisted(() => ({ customers: vi.fn() }))
vi.mock('../configuration/lookups', () => ({
  manualPartnerOptions: look.customers,
  itemOptions: vi.fn(async (q: string) => [{ value: 'CF0092', label: 'CF0092', hint: 'Formula stage 1' },
    { value: 'CF0010', label: 'CF0010', hint: `match for ${q}` }]),
  itemUnits: vi.fn(async (code: string) => code === 'CF0092'
    ? [{ value: 'EA', label: 'EA (1)' }, { value: '2PACK', label: '2PACK (2 EA)' }, { value: 'CASE', label: 'CASE (6 EA)' }]
    : [{ value: 'EA', label: 'EA (1)' }, { value: 'CASE', label: 'CASE (12 EA)' }]),
}))
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
  return render(<MemoryRouter><QueryClientProvider client={qc}><ToastProvider><ViewsProvider client={views}>
    <PermissionProvider can={(k) => keys.includes(k)}><OrdersPage /></PermissionProvider></ViewsProvider></ToastProvider></QueryClientProvider></MemoryRouter>)
}
const lastFilters = () => (api.list.mock.calls.at(-1)![0].filters ?? [])
/** The order sheet's Lines tab, and its lifecycle table. */
const linesOf = async (sheet: HTMLElement) => {
  await openTab(sheet, 'Lines')
  return within(sheet).findByRole('table', { name: 'Order lines' })
}

describe('Orders: Re-check on Review orders', () => {
  beforeEach(() => {
    api.fetchOrder.mockReset(); api.fetchOrder.mockResolvedValue(detail)
    api.list.mockReset(); api.list.mockImplementation(async () => ({ items: [order, pending], total: 2 }))
  })

  it('in the detail: why it is held; Re-check needs sales.order.operate and says that it ran', async () => {
    const { unmount } = renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('9302'))
    let dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/item not in the Item Master \(sku=NOPE/)).toBeInTheDocument()  // Overview: the banner
    await openTab(dialog, 'Lines')
    expect(within(dialog).getByText('unmatched')).toBeInTheDocument()
    expect(within(dialog).queryByText('Re-check')).not.toBeInTheDocument()
    unmount()
    api.resolve.mockResolvedValue({ order, remaining: ['line 5: item not in the Item Master'] })
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByText('9302'))
    dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByText('Re-check'))
    await waitFor(() => expect(api.resolve).toHaveBeenCalledWith(4))
    // the sheet's status row: that it ran, and what is still wrong
    await waitFor(() => expect(noticeIn(dialog)).toHaveTextContent(/re-checked at .*: still 1 problem — line 5: item not in the Item Master/))
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
    await openTab(dialog, 'Lines')
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
    await waitFor(() => expect(noticeIn()).toHaveTextContent(/still 1 problem/))  // the list's status row
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
    fireEvent.click(screen.getByRole('button', { name: 'Reset to the saved view' }))
    await waitFor(() => expect(api.list.mock.calls.at(-1)![0].search).toBeUndefined())
    expect(screen.getByRole('button', { name: 'View: All' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: '9302' } })
    fireEvent.keyDown(screen.getByLabelText('Search'), { key: 'Enter' })
    expect(screen.queryByRole('button', { name: 'Save view' })).toBeNull()  // a system view: only Save as new
    fireEvent.click(await screen.findByRole('button', { name: 'Save as a new view' }))
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
  const sentOrder: OrderRead = { ...pending, id: 6, order_number: 106, source_order_number: '9304', source_reference: 'SHOP-9304', fulfillment_status: '940 Sent' }
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
    await within(dialog).findByRole('tab', { name: /^Activity/ })  // the detail has loaded
    expect(within(dialog).queryByText('Send 940 now')).toBeNull()
    unmount()
    api.send.mockResolvedValue({ order: pending, document_id: 20, file_status: 'Ready To Send', control: '000000123', errors: [] })
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByText('9303'))
    dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByText('Send 940 now'))
    await waitFor(() => expect(api.send).toHaveBeenCalledWith(5, false))
    await waitFor(() => expect(noticeIn(dialog)).toHaveTextContent('940 000000123 queued'))
  })

  it('a sent 940 shows Sent and the 997 apart; a change goes only through a confirmed replacement', async () => {
    api.fetchOrder.mockResolvedValue({ ...detail, ...sentOrder, documents: [out(30, 'Sent', 'Waiting', '000000122')] })
    api.send.mockResolvedValue({ order: sentOrder, document_id: 31, file_status: 'Ready To Send', control: '000000124', errors: [] })
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByText('9304'))
    const dialog = await screen.findByRole('dialog', { name: 'SHOP-9304' })
    await openTab(dialog, 'Documents')
    const table = within(dialog).getByRole('table', { name: 'Documents' })
    const row = within(table).getByText('940-SHOP-000000122.edi').closest('tr')!
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
    await waitFor(() => expect(noticeIn(dialog)).toHaveTextContent(/failed its checks and was not sent.*ship_to.zip is empty/))
  })

  it('Next 940s lists what the next cycle would send, writing nothing', async () => {
    api.eligible.mockResolvedValue([{ order_id: 5, order_number: 105, source_reference: 'SHOP-9303', partner_code: 'SHOP', source_order_number: '9303' }])
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByRole('button', { name: 'Next 940s' }))
    const dialog = await screen.findByRole('dialog', { name: 'Next 940s' })
    expect(await within(dialog).findByText('Order SHOP-9303 · internal no. 105 · SHOP')).toBeInTheDocument()
    expect(api.send).not.toHaveBeenCalled()
  })
})

describe('Orders: manual orders (FR-OB-19a) — New in the standard sheet, drafts as a view', () => {
  const manualDraft: OrderRead = { ...order, id: 9, order_number: 109, partner_code: 'MAN', partner_name: 'Manual orders', source_reference: 'MAN-109', source_type: 'MANUAL', source_order_number: 'REF-9',
    fulfillment_status: 'Draft', order_state: 'Draft', review_reason: null, version: 3,
    ship_to: { country: 'CA', name: 'Store 12', phone: '613-555-0100', address1: '1 Main St', city: 'Ottawa', province: 'ON', zip: 'K1A 0B1' } }
  const shipTo = { country: 'CA', name: 'Jane Doe', phone: '416-555-0100', address1: '10 King St W', city: 'Toronto', province: 'ON', zip: 'M5H 1A1' }
  async function fillShipTo(form: HTMLElement) {
    const st = within(form).getByRole('group', { name: 'Ship to' })
    fireEvent.change(within(st).getByLabelText(/^Name/), { target: { value: 'Jane Doe' } })
    fireEvent.change(within(st).getByLabelText(/^Address\b(?! line)/), { target: { value: '10 King St W' } })
    fireEvent.change(within(st).getByLabelText(/^City/), { target: { value: 'Toronto' } })
    fireEvent.change(within(st).getByLabelText(/^Phone/), { target: { value: '416-555-0100' } })
    await chooseOption(within(st).getByRole('combobox', { name: /^Province/ }), 'Ontario (ON)')
    fireEvent.change(within(st).getByLabelText(/^Postal code/), { target: { value: 'm5h1a1' } })
  }
  beforeEach(() => {
    Object.values(api).forEach((f) => f.mockReset())
    look.customers.mockReset(); look.customers.mockResolvedValue([{ value: 'MAN', label: 'Manual orders', hint: 'MAN' }])
    api.list.mockImplementation(async () => ({ items: [manualDraft], total: 1 }))
    api.fetchOrder.mockResolvedValue({ ...detail, ...manualDraft, requested_delivery_date: null, notes: null, lines: [
      { ...detail.lines[0], id: 91, item_code: 'CF0092', ordered_qty: '2', order_unit: 'CASE', problem: null }] })
  })

  it('New manual order needs sales.order.operate; it previews EA and saves only when every line converts', async () => {
    const { unmount } = renderAs(['sales.read'])
    await screen.findByText('REF-9')
    expect(screen.queryByRole('button', { name: 'New manual order' })).toBeNull()
    unmount()
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByRole('button', { name: 'New manual order' }))
    const form = await screen.findByRole('dialog', { name: 'New manual order' })
    // the only MANUAL customer is chosen already, shown with its code
    expect(await within(form).findByRole('combobox', { name: 'Customer' })).toHaveTextContent('Manual orders — MAN')
    const unit = within(form).getByRole('combobox', { name: 'Unit 1' })
    expect(unit).toBeDisabled()  // until an item is chosen
    await pickSuggestion(within(form).getByRole('combobox', { name: 'Item 1' }), 'formula', /^CF0092/)
    expect(within(form).getByRole('combobox', { name: 'Item 1' })).toHaveTextContent('CF0092 — Formula stage 1')
    await waitFor(() => expect(within(form).getByRole('combobox', { name: 'Unit 1' })).toBeEnabled())
    await chooseOption(within(form).getByRole('combobox', { name: 'Unit 1' }), 'CASE (6 EA)')
    expect(within(form).getByRole('button', { name: 'Save draft' })).toBeDisabled()  // preview first
    api.preview.mockResolvedValueOnce([{ item_code: 'CF0092', quantity: '1', unit: 'CASE', qty_ea: null, problem: 'no conversion for unit CASE' }])
    fireEvent.click(within(form).getByRole('button', { name: 'Preview EA' }))
    expect(await within(form).findByText('no conversion for unit CASE')).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Save draft' })).toBeDisabled()
    api.preview.mockResolvedValueOnce([{ item_code: 'CF0092', quantity: '1', unit: 'CASE', qty_ea: 6, problem: null }])
    fireEvent.click(within(form).getByRole('button', { name: 'Preview EA' }))
    expect(await within(form).findByText('6')).toBeInTheDocument()
    api.create.mockResolvedValue({ ...manualDraft, id: 10, order_number: 110, source_reference: 'MAN-110' })
    fireEvent.click(within(form).getByRole('button', { name: 'Save draft' }))
    // Ship to is required: nothing is saved without it
    const st = within(form).getByRole('group', { name: 'Ship to' })
    expect(await within(st).findAllByText('Required')).toHaveLength(6)  // the phone too: carriers need it
    expect(api.create).not.toHaveBeenCalled()
    await fillShipTo(form)
    fireEvent.click(within(form).getByRole('button', { name: 'Save draft' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledWith({ partner_code: 'MAN', customer_reference: null,
      requested_delivery_date: null, notes: null, ship_to: { ...shipTo, zip: 'm5h1a1' }, lines: [{ item_code: 'CF0092', quantity: 1, unit: 'CASE' }] }))
    expect(await screen.findByRole('dialog', { name: 'MAN-110' })).toBeInTheDocument()  // the new draft opens
  })

  it('a draft is submitted from its row, and edited with the same form in its sheet', async () => {
    api.submit.mockResolvedValue({ ...manualDraft, fulfillment_status: 'Pending' })
    api.update.mockResolvedValue({ ...manualDraft, version: 4 })
    renderAs(['sales.read', 'sales.order.operate'])
    const row = (await screen.findByText('REF-9')).closest('tr')!
    fireEvent.click(within(row).getByRole('button', { name: 'Submit' }))
    await waitFor(() => expect(api.submit).toHaveBeenCalledWith(9, 3))
    expect(screen.queryByRole('dialog')).toBeNull()  // the row action does not open the order
    fireEvent.click(within(row).getByText('REF-9'))
    const sheet = await screen.findByRole('dialog', { name: 'MAN-109' })
    fireEvent.click(await within(sheet).findByRole('button', { name: 'Edit draft' }))
    expect(await within(sheet).findByLabelText('Quantity 1')).toHaveValue(2)  // the draft's own line
    expect(within(within(sheet).getByRole('group', { name: 'Ship to' })).getByLabelText(/^City/)).toHaveValue('Ottawa')  // and address
    api.preview.mockResolvedValueOnce([{ item_code: 'CF0092', quantity: '3', unit: 'CASE', qty_ea: 18, problem: null }])
    fireEvent.change(within(sheet).getByLabelText('Quantity 1'), { target: { value: '3' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Preview EA' }))
    expect(await within(sheet).findByText('18')).toBeInTheDocument()
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(9, expect.objectContaining({ partner_code: 'MAN', customer_reference: 'REF-9',
      ship_to: manualDraft.ship_to, lines: [{ item_code: 'CF0092', quantity: 3, unit: 'CASE' }] }), 3))
  })

  it("the unit offers only the item's units and goes back to EA when the item changes", async () => {
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByRole('button', { name: 'New manual order' }))
    const form = await screen.findByRole('dialog', { name: 'New manual order' })
    await pickSuggestion(await within(form).findByRole('combobox', { name: 'Item 1' }), 'CF', /^CF0092/)
    await waitFor(() => expect(within(form).getByRole('combobox', { name: 'Unit 1' })).toBeEnabled())
    fireEvent.keyDown(within(form).getByRole('combobox', { name: 'Unit 1' }), { key: 'Enter' })
    expect((await screen.findAllByRole('option')).map((o) => o.textContent)).toEqual(['EA (1)', '2PACK (2 EA)', 'CASE (6 EA)'])
    fireEvent.click(screen.getByRole('option', { name: '2PACK (2 EA)' }))
    await waitFor(() => expect(within(form).getByRole('combobox', { name: 'Unit 1' })).toHaveTextContent('2PACK (2 EA)'))
    await pickSuggestion(within(form).getByRole('combobox', { name: 'Item 1' }), 'CF', /^CF0010/)  // no 2PACK there
    await waitFor(() => expect(within(form).getByRole('combobox', { name: 'Unit 1' })).toHaveTextContent('EA (1)'))
  })

  it('with no MANUAL customer, the form says where to add one instead of an empty list', async () => {
    look.customers.mockResolvedValue([])
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter><QueryClientProvider client={qc}><ToastProvider><ViewsProvider client={fakeViews()}>
      <PermissionProvider can={(k) => ['sales.read', 'sales.order.operate'].includes(k)}><OrdersPage /></PermissionProvider>
    </ViewsProvider></ToastProvider></QueryClientProvider></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: 'New manual order' }))
    const form = await screen.findByRole('dialog', { name: 'New manual order' })
    expect(await within(form).findByText(/manual orders use partners whose channel is Manual/)).toBeInTheDocument()
    expect(within(form).getByRole('link', { name: 'Configuration → Partners' })).toHaveAttribute('href', '/config/partners')
    expect(within(form).queryByRole('combobox', { name: 'Customer' })).toBeNull()
  })

  it('with several MANUAL customers none is chosen for you', async () => {
    look.customers.mockResolvedValue([{ value: 'MAN', label: 'Manual orders', hint: 'MAN' }, { value: 'STAFF', label: 'Staff purchases', hint: 'STAFF' }])
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByRole('button', { name: 'New manual order' }))
    const form = await screen.findByRole('dialog', { name: 'New manual order' })
    expect(await within(form).findByRole('combobox', { name: 'Customer' })).not.toHaveTextContent('Manual orders')
  })
})

describe('the order detail: Ship to and Bill to with where they came from', () => {
  beforeEach(() => { Object.values(api).forEach((f) => f.mockReset()); api.list.mockResolvedValue({ items: [pending], total: 1 }) })
  const open = async (d: Partial<OrderDetail>) => {
    api.fetchOrder.mockResolvedValue({ ...detail, ...pending, ...d })
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('9303'))
    return screen.findByRole('dialog', { name: 'SHOP-9303' })
  }
  // Overview: Ship to and Bill to side by side, each a card
  const value = (sheet: HTMLElement, term: string) => within(sheet).getByRole('region', { name: term })

  it('an 850: the ShipTo with its GLN and Walmart DC, the BillTo with its code', async () => {
    const sheet = await open({ source_type: 'EDI_850', dc_number: '6097',
      ship_to: { gln: '0605388000123', name: 'WALMART DC 6097', address1: '6800 Maritz Dr', city: 'Mississauga', province: 'ON', zip: 'L5W 1W2', country: 'CA' },
      bill_to: { name: 'Walmart Canada', code: '0605388000000' } })
    await within(sheet).findByText('6800 Maritz Dr')
    expect(value(sheet, 'Ship to')).toHaveTextContent('WALMART DC 6097')
    expect(value(sheet, 'Ship to')).toHaveTextContent('Mississauga ON L5W 1W2')
    expect(value(sheet, 'Ship to')).toHaveTextContent('From the 850 ShipTo · GLN 0605388000123 · DC 6097')
    expect(value(sheet, 'Bill to')).toHaveTextContent('Walmart Canada')
    expect(value(sheet, 'Bill to')).toHaveTextContent('From the 850 BillTo · code 0605388000000')
  })

  it('Shopify: the shipping address; a manual order: entered by hand, no bill-to', async () => {
    const shop = await open({ ship_to: { name: 'A B', address1: '5 Rue X', city: 'Montréal', province: 'QC', zip: 'H2X 1Y4', country: 'CA' }, bill_to: null })
    await within(shop).findByText('5 Rue X')
    expect(value(shop, 'Ship to')).toHaveTextContent(/5 Rue X.*Montréal QC H2X 1Y4.*From the Shopify shipping address/)
    expect(within(shop).queryByText(/PO box/)).toBeNull()
    cleanup()
    const manual = await open({ source_type: 'MANUAL', ship_to: { name: 'Jane', address1: '1 Main', city: 'Ottawa', province: 'ON', zip: 'K1A 0B1',
      country: 'CA', attention: 'Receiving', phone: '613-555-0100' }, bill_to: null })
    await within(manual).findByText('1 Main')
    expect(value(manual, 'Ship to')).toHaveTextContent(/Jane.*Attn: Receiving.*1 Main.*613-555-0100.*Entered by hand/)
    expect(value(manual, 'Bill to')).toHaveTextContent('—None for a manual order')
  })
})

describe('an imported order shipping to a PO box', () => {
  beforeEach(() => { Object.values(api).forEach((f) => f.mockReset()); api.list.mockResolvedValue({ items: [pending], total: 1 }) })
  it('is flagged next to its Ship to, without holding it', async () => {
    api.fetchOrder.mockResolvedValue({ ...detail, ...pending, ship_to: { name: 'A B', address1: 'C.P. 220', city: 'Gatineau', province: 'QC', zip: 'J8T 7H2' },
      ship_to_flags: ['PO box: the carrier may not deliver there'] })
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    expect(await within(sheet).findByText('PO box: the carrier may not deliver there')).toBeInTheDocument()
  })
})

describe('Orders: the Order ref, the partner by name, and the PO prices', () => {
  beforeEach(() => { Object.values(api).forEach((f) => f.mockReset()); api.list.mockResolvedValue({ items: [pending], total: 1 }) })

  it('the list leads with the Order ref and names the partner; the detail has the internal number and priced lines', async () => {
    api.fetchOrder.mockResolvedValue({ ...detail, ...pending, currency: 'CAD', lines: [
      { ...detail.lines[0], id: 1, line_no: 1, item_code: 'CF0092', ordered_qty: '200', order_unit: 'CASE', unit_price: '179.94', problem: null },
      { ...detail.lines[0], id: 2, line_no: 2, item_code: 'CF0093', ordered_qty: '40', order_unit: 'CASE', unit_price: '150.00', problem: null }] })
    renderAs(['sales.read'])
    const row = (await screen.findByText('SHOP-9303')).closest('tr')!
    expect(row).toHaveTextContent('Shopify')
    expect(within(row).getByText('105')).toBeInTheDocument()  // the Internal no. beside the Order ref
    expect(row).not.toHaveTextContent('(SHOP)')  // the code is in the name's tooltip
    fireEvent.click(within(row).getByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    const value = (term: string) => within(sheet).getByText(term, { selector: 'dt' }).nextElementSibling!
    await within(sheet).findByText('Order ref', { selector: 'dt' })  // Overview
    expect(value('Order ref')).toHaveTextContent('SHOP-9303')
    expect(value('Internal no.')).toHaveTextContent('105')
    expect(value('Partner')).toHaveTextContent('Shopify (SHOP)')  // the detail keeps both
    const lines = await linesOf(sheet)
    expect(lines).toHaveTextContent('179.94')
    expect(lines).toHaveTextContent('35,988.00')   // 200 × 179.94
    expect(lines).toHaveTextContent('Total')
    expect(lines).toHaveTextContent('41,988.00')   // + 40 × 150
  })

  it('one lifecycle row per line: shipped, delivered, invoiced and its state; ▸ shows the pallets and lots', async () => {
    const packed = { shipment_id: 7, document_id: 40, sscc: '00077777770077782891', lot: 'LOT-A', expiry: '2028-05-19', qty_shipped_ea: 1100,
      tracking: '1Z-TRACK', ship_date: '2026-11-02', applied: true, shadow: false }
    api.fetchOrder.mockResolvedValue({ ...detail, ...pending, currency: 'CAD', lines: [
      { ...detail.lines[0], id: 1, line_no: 1, item_code: 'CF0092', ordered_qty: '200', order_unit: 'CASE', unit_price: '179.94', problem: null,
        qty_ea: 1200, shipped_ea: 1200, delivered_qty: 1200, invoiced_qty: 200, invoice_unit: 'CA', invoice_price: '175.00',
        invoice_amount: '35000.00', state: 'Invoiced', packing: [packed, { ...packed, sscc: '00077777770077782907', lot: 'LOT-B', qty_shipped_ea: 100, shadow: true, applied: false }] },
      { ...detail.lines[0], id: 2, line_no: 2, item_code: 'CF0093', ordered_qty: '40', order_unit: 'CASE', unit_price: '150.00', problem: null,
        qty_ea: 480, shipped_ea: 400, delivered_qty: null, state: 'Short shipped', packing: [] }] })
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    const lines = await linesOf(sheet)
    const first = within(lines).getByText('CF0092').closest('tr')!
    expect(first).toHaveTextContent('Invoiced')
    expect(first).toHaveTextContent('200 CA')
    expect(first).toHaveTextContent('35,000.00')
    expect(within(lines).getByText('CF0093').closest('tr')).toHaveTextContent('Short shipped')
    // less-used columns start hidden; the eye shows them
    expect(within(lines).queryByText('Discount')).toBeNull()
    expect(first).toHaveTextContent('175.00 CAD /CA')  // invoiced: the invoice's price per its unit
    // footer: EA, shipped, delivered and invoiced totals
    const foot = lines.querySelector('tfoot')!
    expect(foot).toHaveTextContent('1680')
    expect(foot).toHaveTextContent('1600')
    expect(foot).toHaveTextContent('35,000.00')
    // a line with nothing packed has no ▸
    expect(within(lines).getAllByRole('button', { name: 'Show details' })).toHaveLength(1)
    fireEvent.click(within(first).getByRole('button', { name: 'Show details' }))
    const packing = await within(sheet).findByRole('table', { name: 'Packed for this line' })
    expect(within(packing).getByText('LOT-A').closest('tr')).toHaveTextContent('00077777770077782891')
    expect(within(packing).getByText('LOT-A').closest('tr')).toHaveTextContent('1Z-TRACK')
    expect(within(packing).getByText('LOT-B').closest('tr')).toHaveTextContent('Shadow run')
    expect(packing.querySelector('tfoot')).toHaveTextContent('1200')
  })

  it('the list has the Total column and a footer summed by the server per currency; group headers have theirs', async () => {
    api.list.mockResolvedValue({ items: [{ ...pending, total_amount: '35988.00' }], total: 3,
      totals: [{ currency: 'CAD', count: 2, sums: { total_amount: '36088.00' } }, { currency: 'USD', count: 1, sums: { total_amount: '120.00' } }] })
    renderAs(['sales.read'])
    const row = (await screen.findByText('SHOP-9303')).closest('tr')!
    expect(row).toHaveTextContent('35,988.00 CAD')
    const foot = row.closest('table')!.querySelector('tfoot')!
    expect(foot).toHaveTextContent('Total · 3 rows')
    expect(within(foot).getByText('36,088.00 CAD')).toBeInTheDocument()
    expect(within(foot).getByText('120.00 USD')).toBeInTheDocument()  // never added to CAD
  })

  it('a Shopify order: payments & refunds — summary, channel, tags, transactions, a refund with its lines; list Net and Refunded', async () => {
    api.list.mockResolvedValue({ items: [{ ...pending, total_amount: '113.00', total_refunded: '37.00', net_amount: '76.00',
      shopify_financial_status: 'partially_refunded' }], total: 1 })
    api.fetchOrder.mockResolvedValue({ ...detail, ...pending, currency: 'CAD', source_name: 'web', tags: 'wholesale, vip',
      payment_gateways: ['shopify_payments'], discount_codes: [{ code: 'WELCOME5', type: 'fixed_amount', amount: '5.00' }],
      tax_lines: [{ title: 'HST', rate: 0.13, amount: '13.00' }], subtotal: '100.00', discount: '5.00', shipping: '7.75', tax: '13.00',
      original_total: '113.00', total_refunded: '37.00', net_amount: '76.00', shopify_financial_status: 'partially_refunded',
      payments: [
        { id: 1, shopify_id: 7001, parent_id: null, refund_id: null, kind: 'sale', gateway: 'shopify_payments', status: 'success', amount: '113.00',
          currency: 'CAD', processed_at: '2026-10-29T12:01:00Z', authorization: 'ch_1', payment_id: 'c1.1', error_code: null, test: false },
        { id: 2, shopify_id: 7002, parent_id: 7001, refund_id: 5, kind: 'refund', gateway: 'shopify_payments', status: 'success', amount: '37.00',
          currency: 'CAD', processed_at: '2026-10-30T13:00:00Z', authorization: null, payment_id: null, error_code: null, test: false }],
      refunds: [{ id: 5, shopify_id: 901, refunded_at: '2026-10-30T13:00:00Z', processed_at: null, note: 'damaged in transit', restock: false,
        lines_amount: '28.25', shipping_amount: '8.75', adjustments_amount: '0.00', amount: '37.00',
        lines: [{ line_item_id: 11, order_line_id: 1, sku: 'CF0092-SKU', title: 'Formula', quantity: 1, subtotal: '25.00', total_tax: '3.25', restock_type: 'no_restock' }],
        adjustments: [{ kind: 'shipping_refund', reason: 'Shipping refund', amount: '-7.75', tax_amount: '-1.00' }] }] })
    renderAs(['sales.read'])
    const row = (await screen.findByText('SHOP-9303')).closest('tr')!
    expect(row).toHaveTextContent('37.00 CAD')
    expect(row).toHaveTextContent('76.00 CAD')
    expect(row).toHaveTextContent('partially refunded')
    fireEvent.click(within(row).getByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    await openTab(sheet, 'Payments')
    const money = await within(sheet).findByRole('region', { name: 'Payments and refunds' })
    const value = (term: string) => within(money).getByText(term, { selector: 'dt' }).nextElementSibling!
    expect(value('Sales channel')).toHaveTextContent('web')
    expect(value('Tags')).toHaveTextContent('wholesale')
    expect(value('Tags')).toHaveTextContent('vip')
    expect(value('Discount codes')).toHaveTextContent('WELCOME5 (5.00 CAD)')
    expect(value('Tax')).toHaveTextContent('HST 13%: 13.00 CAD')
    expect(value('Net')).toHaveTextContent('76.00 CAD')
    const pays = within(money).getByRole('table', { name: 'Payments' })
    expect(within(pays).getByText('ch_1').closest('tr')).toHaveTextContent('113.00 CAD')
    expect(within(pays).getAllByRole('row')[2]).toHaveTextContent('-37.00 CAD')
    const refunds = within(money).getByRole('table', { name: 'Refunds' })
    expect(within(refunds).getByText('damaged in transit').closest('tr')).toHaveTextContent('8.75 CAD')
    fireEvent.click(within(refunds).getByRole('button', { name: 'Show details' }))
    expect(within(refunds).getByRole('list', { name: 'Refunded lines' })).toHaveTextContent('1 × CF0092-SKU — 25.00 CAD + 3.25 CAD tax · no restock')
    expect(within(refunds).getByRole('list', { name: 'Refund adjustments' })).toHaveTextContent('shipping refund (Shipping refund): -7.75 CAD')
  })

  it('a retailer order has no Payments & refunds section', async () => {
    api.fetchOrder.mockResolvedValue({ ...detail, ...pending, source_type: 'EDI_850' })
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    await within(sheet).findByRole('tab', { name: /^Lines/ })
    expect(within(sheet).queryByRole('tab', { name: /^Payments/ })).toBeNull()  // a Shopify order's tab only
    expect(within(sheet).queryByRole('region', { name: 'Payments and refunds' })).toBeNull()
  })
})

describe('Orders: backordered quantities and the invoice price before the invoice', () => {
  beforeEach(() => { Object.values(api).forEach((f) => f.mockReset()) })
  // invoiced here (an order whose invoice is Not Required has no invoice price)
  const shipped = { ...pending, fulfillment_status: 'Shipped', order_state: 'Active' as const, backordered_ea: 80,
    invoice_status: 'Waiting for Delivery Confirmation' }
  const line = { ...detail.lines[0], id: 2, line_no: 2, item_code: 'CF0093', ordered_qty: '40', order_unit: 'CASE', unit_price: '150.00',
    problem: null, qty_ea: 480, shipped_ea: 400, backordered_ea: 80, state: 'Short shipped', invoice_price: '150.00', packing: [] }

  it('a partial 945: Backordered EA on the line, in the footer and the list; the order is marked partially shipped', async () => {
    api.list.mockResolvedValue({ items: [shipped], total: 1 })
    api.fetchOrder.mockResolvedValue({ ...detail, ...shipped, currency: 'CAD', has_invoice: false, lines: [line] })
    renderAs(['sales.read'])
    const row = (await screen.findByText('SHOP-9303')).closest('tr')!
    expect(row).toHaveTextContent('Partially shipped')
    expect(row).toHaveTextContent('80')
    fireEvent.click(within(row).getByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    const lines = await linesOf(sheet)
    expect(within(lines).getByText('Backordered EA')).toBeInTheDocument()
    expect(within(lines).getByText('CF0093').closest('tr')).toHaveTextContent('80')
    expect(lines.querySelector('tfoot')).toHaveTextContent('80')
    expect(within(lines).getByText('CF0093').closest('tr')).toHaveTextContent('150.00 CAD /CASE')  // the PO price, per order unit
  })

  it('the invoice price is edited before the invoice: a price other than the PO needs a reason; it is saved per line', async () => {
    api.list.mockResolvedValue({ items: [shipped], total: 1 })
    api.fetchOrder.mockResolvedValue({ ...detail, ...shipped, currency: 'CAD', has_invoice: false, lines: [line] })
    api.prices.mockResolvedValue({ ...detail, ...shipped, currency: 'CAD', has_invoice: false,
      lines: [{ ...line, invoice_unit_price: '145', invoice_price: '145' }] })
    renderAs(['sales.read', 'sales.invoice.manage'])
    fireEvent.click(await screen.findByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    fireEvent.click(await within(sheet).findByRole('button', { name: 'Edit invoice prices' }))
    const price = await within(sheet).findByRole('textbox', { name: 'Invoice price line 1' })  // a Shopify line by its position (line_no 2 is its only line)
    expect(price).toHaveValue('150')
    fireEvent.change(price, { target: { value: '145' } })
    expect(within(sheet).getByText(/vs PO/)).toBeInTheDocument()
    expect(within(sheet).getByText('An invoice price differs from the PO: give the reason.')).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Save prices' })).toBeDisabled()
    fireEvent.change(within(sheet).getByLabelText(/Reason for a price different from the PO/), { target: { value: 'agreed promo' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save prices' }))
    await waitFor(() => expect(api.prices).toHaveBeenCalledWith(5, { reason: 'agreed promo', lines: [{ id: 2, invoice_unit_price: '145' }] }))
    const lines = await linesOf(sheet)
    expect(within(lines).getByText('CF0093').closest('tr')).toHaveTextContent('145.00 CAD /CASE')
  })

  it('once the invoice is drafted the prices are changed on the invoice: no edit on the order', async () => {
    api.list.mockResolvedValue({ items: [shipped], total: 1 })
    api.fetchOrder.mockResolvedValue({ ...detail, ...shipped, currency: 'CAD', has_invoice: true, lines: [line] })
    renderAs(['sales.read', 'sales.invoice.manage'])
    fireEvent.click(await screen.findByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    await linesOf(sheet)
    expect(within(sheet).queryByRole('button', { name: 'Edit invoice prices' })).toBeNull()
    expect(within(sheet).getByText(/change its prices on the invoice/)).toBeInTheDocument()
  })

  it('without sales.invoice.manage there is no price edit', async () => {
    api.list.mockResolvedValue({ items: [shipped], total: 1 })
    api.fetchOrder.mockResolvedValue({ ...detail, ...shipped, currency: 'CAD', has_invoice: false, lines: [line] })
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    await linesOf(sheet)
    const edit = within(sheet).queryByRole('button', { name: 'Edit invoice prices' })
    expect(edit === null || (edit as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('the order sheet: facts, actions and tabs', () => {
  beforeEach(() => { Object.values(api).forEach((f) => f.mockReset()) })
  const shipped = { ...pending, fulfillment_status: 'Shipped', order_state: 'Active' as const, backordered_ea: 80 }
  const line = { ...detail.lines[0], id: 2, line_no: 2, item_code: 'CF0093', ordered_qty: '40', order_unit: 'CASE', unit_price: '150.00',
    problem: null, qty_ea: 480, shipped_ea: 400, backordered_ea: 80, state: 'Short shipped', invoice_price: '150.00', packing: [
      { shipment_id: 7, document_id: 40, sscc: '00077777770077782891', lot: 'LOT-A', expiry: '2028-05-19', qty_shipped_ea: 400,
        tracking: '1Z-TRACK', ship_date: '2026-11-02', applied: true, shadow: false }] }
  const shipment = { id: 7, document_id: 40, order_key_received: '105', ship_date: '2026-11-02', ship_date_source: 'G62', carrier: 'PRLA',
    mode: 'U', tracking: ['1Z-TRACK'], bol: null, our_reference: 'SHOP-9303', applied: true, shadow: false, checks: [],
    shopify_status: 'Done', shopify_error: null, rows: [] }
  const timeline = [{ at: '2026-11-02T15:00:00Z', user_name: 'System', entity: 'sales_orders', action: 'update',
    before: { fulfillment_status: '940 Sent' }, after: { fulfillment_status: 'Shipped' }, reason: '945 945-1.edi (short)' }]

  it('a Shopify order: facts in tiles, Payments shown, Invoice hidden; a short line dots Lines; tracking and lots are copyable chips', async () => {
    api.list.mockResolvedValue({ items: [shipped], total: 1 })
    api.fetchOrder.mockResolvedValue({ ...detail, ...shipped, currency: 'CAD', has_invoice: false, total_amount: '6000.00',
      net_amount: '5963.00', total_refunded: '37.00', lines: [line], shipments: [shipment], timeline, payments: [], refunds: [] })
    renderAs(['sales.read', 'sales.invoice.manage'])
    fireEvent.click(await screen.findByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    expect(within(sheet).getByText('Active · Internal no. 105 · Shopify · PO 9303')).toBeInTheDocument()  // the subtitle: state, the name said once
    const facts = await within(sheet).findByLabelText('Key facts')
    expect(facts).toHaveTextContent('Total6,000.00 CAD1 line')
    expect(facts).toHaveTextContent('Net5,963.00 CAD37.00 CAD refunded')
    expect(facts).toHaveTextContent('Ordered EA480')
    expect(facts).toHaveTextContent('Backordered EA80')
    expect(within(facts).getByText('Backordered EA').closest('[data-tone]')).toHaveAttribute('data-tone', 'warning')
    expect(within(sheet).getByRole('tab', { name: /^Payments/ })).toBeInTheDocument()
    expect(within(sheet).queryByRole('tab', { name: /^Invoice/ })).toBeNull()  // Not Required, none built
    expect(within(sheet).queryByRole('button', { name: 'Edit invoice prices' })).toBeNull()  // never invoiced here
    expect(within(within(sheet).getByRole('tab', { name: /^Lines/ })).getByRole('img', { name: 'needs attention' })).toBeInTheDocument()
    await openTab(sheet, 'Shipments & lots')
    expect(within(sheet).getAllByRole('button', { name: 'Copy 1Z-TRACK' }).length).toBeGreaterThan(0)
    const card = within(sheet).getByRole('group', { name: 'Shipment 7' })
    expect(card).toHaveTextContent('Ship dateNov 2, 2026')  // label: value; the X12 source in a tooltip, not the text
    expect(card).not.toHaveTextContent('G62')
    expect(card).toHaveTextContent('CarrierPRLA · Parcel')
    const lots = within(sheet).getByRole('table', { name: 'Pallets and lots' })
    expect(within(lots).getByRole('button', { name: 'Copy LOT-A' })).toBeInTheDocument()
    await openTab(sheet, 'Activity')
    const activity = within(sheet).getByRole('list', { name: 'Activity' })
    expect(activity).toHaveTextContent('945 945-1.edi (short)')
    expect(activity).toHaveTextContent('fulfillment status: 940 Sent → Shipped')
  })

  it('a retailer order: no Payments; Invoice and Documents tabs; a document downloads; no 945 yet is said in its tab', async () => {
    const ops = await import('@/features/operations/operationsApi')
    const download = vi.spyOn(ops, 'downloadDocument').mockResolvedValue(undefined)
    api.list.mockResolvedValue({ items: [{ ...pending, source_type: 'EDI_850' as const, invoice_status: 'Pending' }], total: 1 })
    api.fetchOrder.mockResolvedValue({ ...detail, ...pending, source_type: 'EDI_850', invoice_status: 'Pending', has_invoice: false,
      documents: [{ id: 9, kind: '850_JSON', direction: 'IN', remote_name: 'PO-1.json', created_at: order.created_at,
        file_status: null, ack_status: null, st02: null, sent_at: null, error: null, ack_detail: null }] })
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'SHOP-9303' })
    await within(sheet).findByRole('tab', { name: /^Invoice/ })
    expect(within(sheet).queryByRole('tab', { name: /^Payments/ })).toBeNull()
    await openTab(sheet, 'Shipments & lots')
    expect(within(sheet).getByText('No 945 yet: the warehouse has not reported a shipment.')).toBeInTheDocument()
    await openTab(sheet, 'Documents')
    fireEvent.click(within(sheet).getByRole('button', { name: 'Download PO-1.json' }))
    expect(download).toHaveBeenCalledWith(9, 'PO-1.json')
    await openTab(sheet, 'Invoice')
    expect(within(sheet).getByText('Not built yet (built at delivery confirmation)')).toBeInTheDocument()
    download.mockRestore()
  })
})
