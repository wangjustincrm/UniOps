import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { OrderDetail, ShipmentRead } from './ordersApi'
import { compare945 } from './orderStatus'

const cancelOrder = vi.fn(async () => ({}))
const releaseHold = vi.fn(async () => ({}))
vi.mock('./ordersApi', async (orig) => ({
  ...(await orig<typeof import('./ordersApi')>()),
  cancelOrder: (...a: unknown[]) => (cancelOrder as (...x: unknown[]) => unknown)(...a),
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
  beforeEach(() => { cancelOrder.mockClear(); releaseHold.mockClear() })

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
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark as shipped' }))
    await waitFor(() => expect(releaseHold).toHaveBeenCalledWith(3, 7, 'Shipped', 'the warehouse confirmed it left'))
  })

  it('945 Conflict: the two side by side, then keep the applied one or cancel', async () => {
    show({ ...base, on_hold_reason: '945 Conflict', hold_exits: ['Shipped', 'Cancelled'], shipments: [ship(1, true, 12, 'A'), ship(2, false, 6, 'B')] })
    fireEvent.click(screen.getByRole('button', { name: 'Compare the 945s' }))
    const table = await screen.findByRole('table', { name: 'The two 945s' })
    expect(table).toHaveTextContent('CF0092 EA: 12')
    expect(table).toHaveTextContent('CF0092 EA: 6')
    expect(screen.getByRole('button', { name: 'Keep the applied 945' })).toBeInTheDocument()
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
