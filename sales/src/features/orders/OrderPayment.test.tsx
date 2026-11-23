import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, RecordActionButtons, ToastProvider, type ActionOut } from '@/platform-ui'
import type { OrderRead } from './ordersApi'
import { ORDER_ACTIONS } from './orderActions'
import { PaymentBadge } from './OrderPayment'

const order = (over: Partial<OrderRead> = {}) => ({
  id: 7, version: 3, order_number: 1018753, source_reference: 'SHP-9617', payment_status: 'Not settled', shadow: false,
  currency: 'CAD', ...over,
}) as OrderRead

function renderAs(keys: string[], ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={(k) => keys.includes(k)}>
    {ui}</PermissionProvider></ToastProvider></QueryClientProvider>)
}
const out = (status: 'done' | 'skipped', dry: boolean): ActionOut => ({ action: 'settle', dry_run: dry,
  results: [{ id: 7, status }], counts: { done: status === 'done' ? 1 : 0, skipped: status === 'skipped' ? 1 : 0, failed: 0 } })

describe('Payment: Mark settled as a record action (user 2026-11-23)', () => {
  it('a finance user marks an order settled: the dry run first, then the run with the date and the reference', async () => {
    const run = vi.fn(async (_id: string, body: { dry_run?: boolean }) => out('done', !!body.dry_run))
    renderAs(['sales.read', 'sales.settle'], <RecordActionButtons record={order()} actions={ORDER_ACTIONS} host={{ run, name: () => 'SHP-9617' }} />)
    expect(screen.queryByRole('button', { name: 'Undo settled' })).toHaveAttribute('aria-disabled', 'true')  // not settled
    fireEvent.click(screen.getByRole('button', { name: 'Mark settled' }))
    const dialog = await screen.findByRole('dialog', { name: 'Mark settled' })
    expect(await within(dialog).findByText('1 will be marked settled')).toBeInTheDocument()
    expect(run).toHaveBeenCalledWith('settle', { ids: { 7: 3 }, dry_run: true })
    fireEvent.change(within(dialog).getByLabelText(/Reference/), { target: { value: 'EFT 55012' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark settled (1)' }))
    await waitFor(() => expect(run).toHaveBeenLastCalledWith('settle', expect.objectContaining({
      ids: { 7: 3 }, input: expect.objectContaining({ reference: 'EFT 55012', amount: null }) })))
  })

  it('the preview says what is left out and why; nothing to run then', async () => {
    const run = vi.fn(async () => ({ ...out('skipped', true), results: [{ id: 7, status: 'skipped' as const, reason: 'already settled on 2026-11-20 (EFT 1)' }] }))
    renderAs(['sales.settle'], <RecordActionButtons record={order()} actions={ORDER_ACTIONS} host={{ run, name: () => 'SHP-9617' }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Mark settled' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('SHP-9617: already settled on 2026-11-20 (EFT 1)')).toBeInTheDocument()
    expect(within(dialog).getByText('It applies to none of them.')).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /Mark settled \(/ })).toBeNull()
  })

  it('without sales.settle the Payment shows and no action does (an operator)', () => {
    renderAs(['sales.read', 'sales.order.operate', 'sales.invoice.manage'], <>
      <PaymentBadge o={order()} /><RecordActionButtons record={order()} actions={ORDER_ACTIONS} host={{ run: vi.fn(), name: () => '' }} /></>)
    expect(screen.getByText('Not settled')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /settled/i })).toBeNull()
  })
})
