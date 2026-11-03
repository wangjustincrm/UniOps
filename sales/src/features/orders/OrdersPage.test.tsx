import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  PermissionProvider, ToastProvider, ViewsProvider, requestView, type ListMeta, type SavedView, type ViewsClient,
} from '@/platform-ui'
import { MemoryRouter } from 'react-router-dom'
import { chooseOption, pickSuggestion } from '@/platform-ui/testing'
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
  preview: vi.fn(), create: vi.fn(), submit: vi.fn(), update: vi.fn() }))
vi.mock('./ordersApi', async (orig) => {
  const real = await orig<typeof import('./ordersApi')>()
  return { ...real, ordersClient: { ...real.ordersClient, list: api.list, meta: async () => meta },
    fetchOrder: api.fetchOrder, resolveReview: api.resolve, send940: api.send, fetchEligibility: api.eligible,
    previewManual: api.preview, createManual: api.create, submitManual: api.submit, updateManual: api.update }
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
    const dialog = await screen.findByRole('dialog', { name: 'Order SHOP-9304' })
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
    api.eligible.mockResolvedValue([{ order_id: 5, order_number: 105, source_reference: 'SHOP-9303', partner_code: 'SHOP', source_order_number: '9303' }])
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByRole('button', { name: 'Next 940s' }))
    const dialog = await screen.findByRole('dialog', { name: 'Next 940s' })
    expect(await within(dialog).findByText('Order SHOP-9303 · SHOP')).toBeInTheDocument()
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
    expect(await screen.findByRole('dialog', { name: 'Order MAN-110' })).toBeInTheDocument()  // the new draft opens
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
    const sheet = await screen.findByRole('dialog', { name: 'Order MAN-109' })
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
    expect(await within(form).findByRole('status')).toHaveTextContent('manual orders use partners whose channel is Manual')
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
    return screen.findByRole('dialog', { name: 'Order SHOP-9303' })
  }
  const value = (sheet: HTMLElement, term: string) => within(sheet).getByText(term, { selector: 'dt' }).nextElementSibling!

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
    const sheet = await screen.findByRole('dialog', { name: 'Order SHOP-9303' })
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
    expect(row).toHaveTextContent('Shopify (SHOP)')
    fireEvent.click(within(row).getByText('SHOP-9303'))
    const sheet = await screen.findByRole('dialog', { name: 'Order SHOP-9303' })
    const value = (term: string) => within(sheet).getByText(term, { selector: 'dt' }).nextElementSibling!
    await within(sheet).findByRole('table', { name: 'Order lines' })
    expect(value('Order ref')).toHaveTextContent('SHOP-9303')
    expect(value('Internal no.')).toHaveTextContent('105')
    const lines = within(sheet).getByRole('table', { name: 'Order lines' })
    expect(lines).toHaveTextContent('179.94')
    expect(lines).toHaveTextContent('35,988.00')   // 200 × 179.94
    expect(lines).toHaveTextContent('Lines total')
    expect(lines).toHaveTextContent('41,988.00')   // + 40 × 150
  })
})

