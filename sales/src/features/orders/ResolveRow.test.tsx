import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import { ResolveRow } from './ResolveRow'
import type { LineRead, ShipmentRowRead } from './ordersApi'

const line = { id: 7, line_no: 1, item_code: 'CF0010', qty_ea: 12, removed: false } as unknown as LineRead

function show(matching: number[]) {
  const r = { id: 1, item_code: 'CRMMARK002', qty_shipped_ea: 1, matching_lines: matching } as unknown as ShipmentRowRead
  render(<QueryClientProvider client={new QueryClient()}><ToastProvider><PermissionProvider can={() => true}>
    <ResolveRow r={r} shipmentId={2} orderId={3} lines={[line]} /></PermissionProvider></ToastProvider></QueryClientProvider>)
  fireEvent.click(screen.getByRole('button', { name: 'Resolve' }))
}

describe('Resolve a 945 row', () => {
  it('offers only Accept when no line is the same item', async () => {
    show([])
    expect(await screen.findByText(/can only be accepted/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Link to this line' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Accept without a line' })).toBeInTheDocument()
  })

  it('offers the link when a line is the same item', async () => {
    show([7])
    expect(await screen.findByRole('button', { name: 'Link to this line' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Accept without a line' })).toBeInTheDocument()
  })
})
