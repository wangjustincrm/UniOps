import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { PermissionProvider, RecordSheetHostContext, ToastProvider } from '@/platform-ui'
import type { ConnectionRead } from '@/features/configuration/connections/connectionsApi'
import type { StockCountRead } from '@/features/inventory/inventoryApi'
import type { TransferDetail } from '@/features/transfers/transfersApi'

// The forms that had kept no version of what they show (2026-11-25): a record that moves while one is typed in keeps what was
// typed, says so, and is sent with the version its content was read with — never the record's latest.
const api = vi.hoisted(() => ({ resolve: vi.fn(), update: vi.fn(), count: vi.fn() }))
vi.mock('@/features/transfers/transfersApi', async (orig) => ({ ...await orig<object>(), resolveTransfer: api.resolve }))
vi.mock('@/features/inventory/inventoryApi', async (orig) => ({ ...await orig<object>(), updateCount: api.count,
  fetchCountLines: async () => [], fetchCountCompare: async () => [] }))
vi.mock('@/features/configuration/connections/connectionsApi', async (orig) => {
  const real = await orig<typeof import('@/features/configuration/connections/connectionsApi')>()
  return { ...real, connectionsClient: { ...real.connectionsClient, update: api.update } }
})
vi.mock('@/features/operations/operationsApi', async (orig) => {
  const real = await orig<typeof import('@/features/operations/operationsApi')>()
  return { ...real, runsClient: { ...real.runsClient, list: async () => ({ items: [], total: 0 }) } }
})
const { ResolveForm } = await import('@/features/transfers/TransferForms')
const { CountSheet } = await import('@/features/inventory/CountSheet')
const { ConnectionSchedule } = await import('@/features/configuration/connections/ConnectionSchedule')

const STALE = Object.assign(new Error('stale'), { body: { error: { code: 'entity.stale', message: 'changed by someone else', details: {} } } })
const MEANWHILE = 'Someone else saved this record meanwhile'
const wrap = (ui: React.ReactNode, qc: QueryClient) => (
  <MemoryRouter><QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={() => true}>
    <RecordSheetHostContext.Provider value={{ open: true, close: () => {} }}>{ui}</RecordSheetHostContext.Provider></PermissionProvider></ToastProvider></QueryClientProvider></MemoryRouter>)
const show = (ui: (v: number) => React.ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { rerender } = render(wrap(ui(1), qc))
  return (v: number) => rerender(wrap(ui(v), qc))
}

describe('a form whose record moves while it is typed in', () => {
  it('settling a transfer', async () => {
    api.resolve.mockRejectedValue(STALE)
    const moves = show((v) => <ResolveForm t={{ id: 5, version: v } as TransferDetail} onSaved={() => {}} onCancel={() => {}} />)
    fireEvent.change(screen.getByLabelText(/^Why/), { target: { value: 'ours was wrong' } })
    fireEvent.click(screen.getByRole('radio', { name: /Our 943 was wrong/ }))
    moves(2)
    expect(await screen.findByText(MEANWHILE)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Settle the difference' }))
    await waitFor(() => expect(api.resolve).toHaveBeenCalledWith(5, 1, 'corrected', 'ours was wrong'))
    expect(screen.getByLabelText(/^Why/)).toHaveValue('ours was wrong')
  })

  it("a connection's schedule", async () => {
    api.update.mockRejectedValueOnce(STALE).mockResolvedValueOnce({ id: 7, version: 3 })
    const conn = { id: 7, name: 'c', kind: 'SFTP', schedule_minutes: 5, schedule_paused: false, active_from: null, active_to: '22:00:00', active_days: null } as unknown as ConnectionRead
    const moves = show((v) => <ConnectionSchedule connection={{ ...conn, version: v, active_from: v === 1 ? '06:00:00' : '07:00:00' }} />)
    const from = () => screen.getByLabelText(/^From/)
    fireEvent.change(from(), { target: { value: '05:30' } })
    moves(2)
    expect(await screen.findByText(MEANWHILE)).toBeInTheDocument()
    expect(from()).toHaveValue('05:30')  // kept, not remounted away
    fireEvent.click(screen.getByRole('button', { name: 'Save schedule' }))
    await waitFor(() => expect(api.update.mock.calls.map((c) => c[2])).toEqual([1]))
    fireEvent.click(screen.getByRole('button', { name: /^Reload/ }))
    await waitFor(() => expect(from()).toHaveValue('07:00'))
    fireEvent.change(from(), { target: { value: '08:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save schedule' }))
    await waitFor(() => expect(api.update.mock.calls.map((c) => c[2])).toEqual([1, 2]))
    await new Promise((r) => setTimeout(r, 60))  // saved
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save schedule' })).toBeEnabled())
    fireEvent.change(from(), { target: { value: '09:00' } })  // typed again before its own save has come back
    moves(3)  // and now it has: not someone else's
    await new Promise((r) => setTimeout(r, 30))
    expect(screen.queryByText(MEANWHILE)).toBeNull()
    expect(from()).toHaveValue('09:00')  // and what was typed since is still here
  })

  it("a count's note", async () => {
    api.count.mockRejectedValue(STALE)
    const row = { id: 3, counted_at: '2026-11-20T15:00:00Z', source: 'USER', note: 'first', file_name: 'i.csv', document_id: null, applied_at: null,
      applied_by_name: null, lines: 0, total_ea: 0, hold_ea: 0, created_at: '2026-11-20T15:00:00Z', created_by_name: 'A' } as Omit<StockCountRead, 'version'>
    const moves = show((v) => <CountSheet row={{ ...row, version: v } as StockCountRead} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Change…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Change the count' })
    fireEvent.change(within(dialog).getByLabelText(/^Note/), { target: { value: 'mine' } })
    moves(2)
    expect(await within(dialog).findByText(MEANWHILE)).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/^Note/)).toHaveValue('mine')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.count).toHaveBeenCalledWith(3, 1, expect.any(String), 'mine'))
  })
})
