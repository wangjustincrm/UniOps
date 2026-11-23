import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { OrderRead } from './ordersApi'

const api = vi.hoisted(() => ({ settle: vi.fn(), unsettle: vi.fn(), bulk: vi.fn() }))
vi.mock('./ordersApi', async (orig) => ({ ...(await orig<typeof import('./ordersApi')>()),
  settleOrder: api.settle, unsettleOrder: api.unsettle, settleBulk: api.bulk }))
const { PaymentBadge, SettleActions, SettleShown } = await import('./OrderPayment')

const order = (over: Partial<OrderRead> = {}) => ({
  id: 7, version: 3, order_number: 1018753, source_reference: 'SHP-9617', payment_status: 'Not settled', shadow: false,
  currency: 'CAD', ...over,
}) as OrderRead

function renderAs(keys: string[], ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => keys.includes(k)}>
    {ui}</PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Payment: Settled by Finance (user 2026-11-23)', () => {
  it('a finance user marks an order settled with a date and a reference', async () => {
    api.settle.mockResolvedValue(order({ payment_status: 'Settled' }))
    renderAs(['sales.read', 'sales.settle'], <SettleActions o={order()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Mark settled' }))
    const dialog = await screen.findByRole('dialog', { name: 'Mark SHP-9617 settled' })
    fireEvent.change(within(dialog).getByLabelText(/Reference/), { target: { value: 'EFT 55012' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark settled' }))
    await waitFor(() => expect(api.settle).toHaveBeenCalledWith(7, 3, expect.objectContaining({ reference: 'EFT 55012', amount: null })))
    expect(api.settle.mock.calls[0][2].settled_on).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('without sales.settle the Payment shows and no action does (an operator)', () => {
    renderAs(['sales.read', 'sales.order.operate', 'sales.invoice.manage'], <>
      <PaymentBadge o={order()} /><SettleActions o={order()} /><SettleShown rows={[order()]} refresh={async () => {}} /></>)
    expect(screen.getByText('Not settled')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /settled/i })).toBeNull()
  })

  it('a settled order offers Undo settled; bulk counts only what is not settled yet', () => {
    renderAs(['sales.read', 'sales.settle'], <>
      <SettleActions o={order({ payment_status: 'Settled', settled_on: '2026-11-20', settled_reference: 'EFT 1' })} />
      <SettleShown rows={[order(), order({ id: 8, payment_status: 'Settled' }), order({ id: 9, payment_status: 'Overdue' }),
        order({ id: 10, payment_status: 'Not needed' })]} refresh={async () => {}} /></>)
    expect(screen.getByRole('button', { name: 'Undo settled' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mark shown settled (2)…' })).toBeInTheDocument()
  })
})
