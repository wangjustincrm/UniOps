import { openTab } from '@/platform-ui/testing'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider, ViewsProvider } from '@/platform-ui'
import { type ListMeta } from '@/platform-ui/resource/listMeta'
import { type ViewsClient } from '@/platform-ui/resource/viewsApi'
import { pickSuggestion } from '@/platform-ui/testing'
import type { TransferDetail, TransferRead } from './transfersApi'

const t104: TransferRead = { id: 1, transfer_number: '00104', from_warehouse_id: 1, to_warehouse_id: 2, status: 'Discrepancy', notes: null,
  document_id: 5, error: null, version: 4, created_at: '2026-10-07T13:00:00Z', created_by_name: 'Op', updated_at: null,
  from_warehouse: 'KGN', to_warehouse: 'CLN02' }
const detail: TransferDetail = { ...t104, ack_status: 'Accepted', file_status: 'Sent', control: '000000104',
  lines: [{ id: 1, line_no: 1, item_code: 'CF0092', qty_ea: 8400, received_ea: 8400, line_status: 'Received' },
    { id: 2, line_no: 2, item_code: 'CF0093', qty_ea: 7200, received_ea: 0, line_status: 'Open' }],
  receipts: [{ id: 9, document_id: 7, receipt_date: '2026-10-08', total_ea: 15600, applied: true, shadow: false, outcome: 'Discrepancy',
    discrepancies: ['CF0103 7200 EA received but not on the transfer', 'line 2 CF0093: 0 of 7200 EA received'], created_at: '2026-10-08T10:24:00Z',
    document_name: '944-16468.edi', rows: [
      { position: 1, lx_number: '1', item_code: 'CF0092', upc: '628347940140', lot: '26C253125110231', description: null, qty_ea: 600, transfer_line_no: 1 },
      { position: 15, lx_number: '2', item_code: 'CF0103', upc: '628347940362', lot: '26E248121010211', description: null, qty_ea: 600, transfer_line_no: null }] }] }
const draft: TransferRead = { ...t104, id: 2, transfer_number: '00105', status: 'Draft', version: 1 }
const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }

const api = vi.hoisted(() => ({ list: vi.fn(), fetch: vi.fn(), create: vi.fn(), cancel: vi.fn(), ready: vi.fn(), update: vi.fn() }))
vi.mock('./transfersApi', async (orig) => {
  const real = await orig<typeof import('./transfersApi')>()
  return { ...real, transfersClient: { ...real.transfersClient, list: api.list, meta: async () => meta },
    fetchTransfer: api.fetch, createTransfer: api.create, cancelTransfer: api.cancel, readyTransfer: api.ready, updateTransfer: api.update }
})
vi.mock('../configuration/lookups', () => ({
  itemOptions: async () => [{ value: 'CF0092', label: 'CF0092' }],
  warehouseOptions: async () => [{ value: '1', label: 'KGN' }, { value: '2', label: 'CLN02' }],
}))
const { default: TransfersPage } = await import('./TransfersPage')

const views: ViewsClient = { list: vi.fn(async () => []), create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><ViewsProvider client={views}>
    <PermissionProvider can={(k) => keys.includes(k)}><TransfersPage /></PermissionProvider></ViewsProvider></ToastProvider></QueryClientProvider>)
}

describe('Transfers', () => {
  beforeEach(() => {
    Object.values(api).forEach((f) => f.mockReset())
    api.list.mockResolvedValue({ items: [t104, draft], total: 2 })
  })

  it('a received transfer shows its lines, the 944 rows with the one not on the transfer, and the discrepancies', async () => {
    api.fetch.mockResolvedValue(detail)
    renderAs(['sales.read', 'sales.transfer.manage'])
    fireEvent.click(await screen.findByText('00104'))
    const dialog = await screen.findByRole('dialog')
    await openTab(dialog, 'Lines')
    const lines = await within(dialog).findByRole('table', { name: 'Transfer lines' })
    expect(within(lines).getByText('CF0093').closest('tr')).toHaveTextContent('Open')
    await openTab(dialog, 'Receipts')
    const rows = within(dialog).getByRole('table', { name: '944 944-16468.edi lines' })
    expect(within(rows).getByText('CF0103').closest('tr')).toHaveTextContent('not on the transfer')
    expect(within(dialog).getByText('CF0103 7200 EA received but not on the transfer')).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'More actions' })).toBeNull()  // no Cancel: the 943 is sent
  })

  it('a new draft: warehouses and lines; Save needs both warehouses and a quantity', async () => {
    api.create.mockResolvedValue({ ...detail, ...draft })
    renderAs(['sales.read', 'sales.transfer.manage'])
    fireEvent.click(await screen.findByRole('button', { name: 'New transfer' }))
    const form = await screen.findByRole('dialog', { name: 'New transfer' })  // the standard sheet
    const save = within(form).getByRole('button', { name: 'Save draft' })
    expect(save).toBeDisabled()
    await pickSuggestion(within(form).getByRole('combobox', { name: 'From warehouse' }), 'K', /^KGN/)
    await pickSuggestion(within(form).getByRole('combobox', { name: 'To warehouse' }), 'C', /^CLN02/)
    await pickSuggestion(within(form).getByRole('combobox', { name: 'Item 1' }), 'CF00', /^CF0092/)
    expect(within(form).getByRole('alert')).toHaveTextContent('at least 1 EA')
    fireEvent.change(within(form).getByLabelText('EA 1'), { target: { value: '600' } })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(api.create).toHaveBeenCalledWith({ from_warehouse_id: 1, to_warehouse_id: 2, notes: null,
      lines: [{ item_code: 'CF0092', qty_ea: 600 }] }))
  })

  it('a draft is cancelled with a reason; a reader sees no actions', async () => {
    api.fetch.mockResolvedValue({ ...detail, ...draft, receipts: [] })
    api.cancel.mockResolvedValue({ ...detail, ...draft, status: 'Cancelled', receipts: [] })
    const { unmount } = renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('00105'))
    let dialog = await screen.findByRole('dialog')
    await within(dialog).findByRole('tab', { name: /^Lines/ })
    expect(within(dialog).queryByRole('button', { name: 'More actions' })).toBeNull()  // Cancel is under More, with the permission
    expect(within(dialog).queryByRole('button', { name: 'Edit draft' })).toBeNull()
    unmount()
    renderAs(['sales.read', 'sales.transfer.manage'])
    fireEvent.click(await screen.findByText('00105'))
    dialog = await screen.findByRole('dialog')
    fireEvent.keyDown(await within(dialog).findByRole('button', { name: 'More actions' }), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Cancel transfer' }))
    const ask = await screen.findByRole('dialog', { name: 'Cancel transfer 00105?' })
    expect(within(ask).getByRole('button', { name: 'Cancel transfer' })).toBeDisabled()  // a reason first
    fireEvent.change(within(ask).getByLabelText(/Why/), { target: { value: 'not needed' } })
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel transfer' }))
    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith(2, 1, 'not needed'))
  })

  it('a draft is edited with the same form, inside its sheet', async () => {
    const d = { ...detail, ...draft, receipts: [], lines: [{ id: 5, line_no: 1, item_code: 'CF0092', qty_ea: 600, received_ea: 0, line_status: 'Open' }] }
    api.fetch.mockResolvedValue(d)
    api.update.mockResolvedValue({ ...d, version: 2, lines: [{ ...d.lines[0], qty_ea: 1200 }] })
    renderAs(['sales.read', 'sales.transfer.manage'])
    fireEvent.click(await screen.findByText('00105'))
    const sheet = await screen.findByRole('dialog', { name: '00105' })
    fireEvent.click(await within(sheet).findByRole('button', { name: 'Edit draft' }))
    fireEvent.change(await within(sheet).findByLabelText('EA 1'), { target: { value: '1200' } })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(2, 1, { from_warehouse_id: 1, to_warehouse_id: 2, notes: null,
      lines: [{ item_code: 'CF0092', qty_ea: 1200 }] }))
  })
})
