import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { OrderDetail, ShipmentRead } from './ordersApi'
import { applied945, compare945 } from './orderStatus'

const cancelOrder = vi.fn(async () => ({}))
const releaseHold = vi.fn(async () => ({}))
const applyLater945 = vi.fn(async () => ({}))
const SENDS = 'the Shopify write its hold stopped, when released to Shipped'
vi.mock('./ordersApi', async (orig) => ({
  ...(await orig<typeof import('./ordersApi')>()),
  ordersClient: { basePath: '/orders', meta: async () => ({ version: 1, fields: [], searchable: false, default_sort: [], capabilities: { export: null, suggest: false,
    actions: [{ id: 'release_hold', allowed: true, sends: SENDS }] } }) },
  cancelOrder: (...a: unknown[]) => (cancelOrder as (...x: unknown[]) => unknown)(...a),
  applyLater945: (...a: unknown[]) => (applyLater945 as (...x: unknown[]) => unknown)(...a),
  releaseHold: (...a: unknown[]) => (releaseHold as (...x: unknown[]) => unknown)(...a),
}))
const { OrderHoldActions } = await import('./OrderHoldActions')

const ship = (id: number, applied: boolean, qty: number, lot: string) => ({
  id, document_id: id, applied, shadow: false, ship_date: '2026-11-02', tracking: ['T1'],
  rows: [{ item_code: 'CF0092', qty_shipped_ea: qty, lot }],
}) as unknown as ShipmentRead
const base = { id: 3, version: 7, order_number: 3, source_reference: 'SHP-3', on_hold_reason: null, shipments: [], cancellable: false, hold_exits: [] } as unknown as OrderDetail

function show(o: OrderDetail, keys = ['sales.order.operate']) {
  const qc = new QueryClient()
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => keys.includes(k)}><OrderHoldActions o={o} /></PermissionProvider></ToastProvider></QueryClientProvider>)
}
const reason = async (text: string) => {
  const dialog = await screen.findByRole('alertdialog').catch(() => screen.findAllByRole('dialog').then((d) => d[d.length - 1]))
  fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: text } })
  return dialog
}

describe('Cancel order and Release hold', () => {
  beforeEach(() => { cancelOrder.mockClear(); releaseHold.mockClear(); applyLater945.mockClear() })

  it('offers nothing when the server opens nothing (a shadow order, a shipped order)', () => {
    show(base)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('needs the permission', () => {
    show({ ...base, cancellable: true }, [])
    expect(screen.queryByRole('button', { name: 'Cancel order' })).toBeNull()
  })

  it('Cancel order says it writes nothing outside Sales and needs a reason', async () => {
    show({ ...base, cancellable: true })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel order' }))
    const dialog = await reason('agreed')
    expect(dialog).toHaveTextContent('nothing is written to Shopify, the warehouse or any partner')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel order' }))
    await waitFor(() => expect(cancelOrder).toHaveBeenCalledWith(3, 7, 'agreed'))
  })

  it('Cancelled at Source: the exits the server gives, with a reason', async () => {
    show({ ...base, cancellable: true, on_hold_reason: 'Cancelled at Source', hold_exits: ['Cancelled', 'Shipped', '940 Sent'] })
    expect(screen.queryByRole('button', { name: 'Cancel order' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Release hold' }))
    expect(await screen.findByRole('button', { name: 'Cancel the order' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Back to 940 Sent' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Mark as shipped' }))
    const dialog = await reason('the warehouse confirmed it left')
    expect(dialog).toHaveTextContent(`Sends ${SENDS}.`)  // Shipped is where something may leave
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark as shipped' }))
    await waitFor(() => expect(releaseHold).toHaveBeenCalledWith(3, 7, 'Shipped', 'the warehouse confirmed it left'))
  })

  it('an exit that sends nothing says nothing of sending; one left out or stale is an error, not a success', async () => {
    releaseHold.mockRejectedValueOnce(Object.assign(new Error('x'), { body: { error: { code: 'order.hold_exit', message: 'not on hold', details: {} } } }))
    show({ ...base, on_hold_reason: 'Cancelled at Source', hold_exits: ['940 Sent'] })
    fireEvent.click(screen.getByRole('button', { name: 'Release hold' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Back to 940 Sent' }))
    const dialog = await reason('it goes on')
    expect(dialog).not.toHaveTextContent('Sends')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Back to 940 Sent' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('That choice is no longer open for this order')
    expect(within(dialog).getByRole('textbox')).toHaveValue('it goes on')
  })

  it('945 Conflict: the two side by side, then keep the applied one or cancel', async () => {
    show({ ...base, on_hold_reason: '945 Conflict', hold_exits: ['Shipped', 'Cancelled'], shipments: [ship(1, true, 12, 'A'), ship(2, false, 6, 'B')] })
    fireEvent.click(screen.getByRole('button', { name: 'Compare the 945s' }))
    const table = await screen.findByRole('table', { name: 'The two 945s' })
    expect(table).toHaveTextContent('CF0092 EA: 12')
    expect(table).toHaveTextContent('CF0092 EA: 6')
    expect(screen.getByRole('button', { name: 'Keep the applied 945' })).toBeInTheDocument()
  })

  const held = { ...base, on_hold_reason: '945 Conflict', hold_exits: ['Shipped', 'Cancelled'],
    shipments: [ship(1, true, 8, 'A'), ship(2, false, 4, 'B'), ship(3, false, 8, 'A')] } as OrderDetail  // 3: a resend of 1
  const open = async (o: OrderDetail) => { show(o); fireEvent.click(screen.getByRole('button', { name: 'Compare the 945s' })); await screen.findByRole('table', { name: 'The two 945s' }) }

  it('a missing part: the server names the 945, the press carries it with a reason', async () => {
    await open({ ...held, later_945: { shipment_id: 2, kind: 'part', items: [{ item_code: 'CF0092', carried_ea: 12, earlier_ea: 8, this_ea: 4, above_ea: 0 }] } })
    expect(screen.getByRole('table', { name: 'The two 945s' })).toHaveTextContent('CF0092 EA: 4')  // 2, not the resend
    expect(screen.queryByRole('button', { name: 'Apply, more than ordered' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Apply the missing part' }))
    const dialog = await reason('the warehouse sent the rest')
    expect(dialog).toHaveTextContent('CF0092: 4 EA is added')
    expect(dialog).toHaveTextContent('Nothing is written to Shopify for it and nobody is emailed about it')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply the missing part' }))
    await waitFor(() => expect(applyLater945).toHaveBeenCalledWith(3, 7, 'part', 2, 'the warehouse sent the rest'))
    expect(releaseHold).not.toHaveBeenCalled()
  })

  it('more than ordered: says item by item how much, and that a corrected 945 must not be applied', async () => {
    await open({ ...held, later_945: { shipment_id: 2, kind: 'more', items: [{ item_code: 'CF0092', carried_ea: 12, earlier_ea: 8, this_ea: 5, above_ea: 1 },
      { item_code: 'CF0093', carried_ea: 6, earlier_ea: 2, this_ea: 4, above_ea: 0 }] } })
    expect(screen.queryByRole('button', { name: 'Apply the missing part' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Apply, more than ordered' }))
    const dialog = await reason('an extra can')
    expect(dialog).toHaveTextContent('CF0092: 1 EA above the 12 EA on the 940 (13 EA in all)')
    expect(dialog).not.toHaveTextContent('CF0093')
    expect(dialog).toHaveTextContent('a corrected 945 of the same goods (another lot or date) must not be applied')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply, more than ordered' }))
    await waitFor(() => expect(applyLater945).toHaveBeenCalledWith(3, 7, 'more', 2, 'an extra can'))
  })

  it('refused: the reason is shown, and nothing to add', async () => {
    await open({ ...held, later_945: { shipment_id: 2, kind: null, refused: 'an order was made from this one', items: [] } })
    expect(screen.getByText('The second 945 cannot be added here: an order was made from this one.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Apply/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Keep the applied 945' })).toBeInTheDocument()
  })

  it('a third 945 is compared with both that count', () => {
    const rows = compare945(applied945([ship(1, true, 8, 'A'), ship(2, true, 4, 'B'), ship(3, false, 1, 'C')])!, ship(3, false, 1, 'C'))
    expect(rows.map((r) => r.left)).toEqual(expect.arrayContaining(['CF0092 EA: 12', 'CF0092 lots: A, B']))
  })

  it('compare945 marks only what differs', () => {
    const rows = compare945(ship(1, true, 12, 'A'), ship(2, false, 6, 'A'))
    expect(rows.map((r) => r.same)).toEqual([true, false, true, true])
  })
})

describe('a test-run refusal', () => {
  it('ends the dialog and goes to the status row, not the dialog', async () => {
    cancelOrder.mockRejectedValueOnce({ body: { error: { code: 'order.shadow', message: 'Zoho handles this order, so nothing was sent or changed.' } } })
    const qc = new QueryClient()
    render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={() => true}>
      <OrderHoldActions o={{ ...base, cancellable: true }} /></PermissionProvider></ToastProvider></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel order' }))
    const dialog = await reason('agreed')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel order' }))
    await waitFor(() => expect(cancelOrder).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())  // the dialog closed
  })
})
