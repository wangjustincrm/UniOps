import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { ColumnDef } from '@/platform-ui'
import { hiddenPaymentColumns, paymentColumns } from './paymentsColumns'
import type { PaymentRowRead } from './paymentsApi'

const key = (c: ColumnDef<PaymentRowRead, unknown>) => (c as { accessorKey?: string; id?: string }).accessorKey ?? (c as { id?: string }).id

// User 2026-11-16 (Finance): one list; the columns it asked for shown, discount / shipping and the rest hidden.
describe('payments columns', () => {
  const cols = paymentColumns(() => {})
  it('shows the asked-for columns and hides the extra ones', () => {
    const shown = cols.map(key).filter((k) => !hiddenPaymentColumns.includes(k!))
    expect(shown).toEqual(['processed_at', 'order_ref', 'customer', 'method', 'kind', 'status', 'amount', 'sales_amount', 'refund_amount',
      'net_amount', 'gateway_ref', 'order_source', 'financial_status', 'issue'])
    expect(hiddenPaymentColumns.every((h) => cols.map(key).includes(h))).toBe(true)
  })

  it('the Order ref opens the order', () => {
    const open = vi.fn()
    const ref = paymentColumns(open).find((c) => key(c) === 'order_ref')!
    const row = { order_id: 42, order_ref: 'SHP-9662' } as PaymentRowRead
    const Cell = ref.cell as (p: { row: { original: PaymentRowRead } }) => ReactNode
    render(<>{Cell({ row: { original: row } })}</>)
    fireEvent.click(screen.getByRole('button', { name: 'SHP-9662' }))
    expect(open).toHaveBeenCalledWith(42)
  })
})
