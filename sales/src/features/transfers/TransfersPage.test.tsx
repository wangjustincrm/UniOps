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
  from_warehouse: 'KGN', to_warehouse: 'CLN02', sent_ea: 15600, received_ea: 8400, items: 'CF0092, CF0093', receipt_date: '2026-10-08', attachments: 1 }
const detail: TransferDetail = { ...t104, ack_status: 'Accepted', file_status: 'Sent', control: '000000104', status_before_hold: null,
  documents: [{ id: 7, kind: '944_EDI', direction: 'IN', remote_name: '944-16468.edi', created_at: '2026-10-08T10:24:00Z', file_status: null,
    ack_status: null, st02: null, sent_at: null, error: null, ack_detail: null }],
  timeline: [{ at: '2026-10-08T10:24:00Z', user_name: 'System', entity: 'sales_transfers', action: 'update', before: { status: 'Waiting for 944' },
    after: { status: 'Discrepancy' }, reason: '944 944-16468.edi (FR-OB-84)' }],
  lines: [{ id: 1, line_no: 1, item_code: 'CF0092', qty_ea: 8400, received_ea: 8400, line_status: 'Received' },
    { id: 2, line_no: 2, item_code: 'CF0093', qty_ea: 7200, received_ea: 0, line_status: 'Open' }],
  receipts: [{ id: 9, document_id: 7, receipt_date: '2026-10-08', total_ea: 15600, applied: true, shadow: false, outcome: 'Discrepancy',
    discrepancies: ['CF0103 7200 EA received but not on the transfer', 'line 2 CF0093: 0 of 7200 EA received'], created_at: '2026-10-08T10:24:00Z',
    document_name: '944-16468.edi', rows: [
      { position: 1, lx_number: '1', item_code: 'CF0092', upc: '628347940140', lot: '26C253125110231', description: null, qty_ea: 600, transfer_line_no: 1 },
      { position: 15, lx_number: '2', item_code: 'CF0103', upc: '628347940362', lot: '26E248121010211', description: null, qty_ea: 600, transfer_line_no: null }] }] }
const draft: TransferRead = { ...t104, id: 2, transfer_number: '00105', status: 'Draft', version: 1 }
const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }

const api = vi.hoisted(() => ({ list: vi.fn(), fetch: vi.fn(), create: vi.fn(), cancel: vi.fn(), ready: vi.fn(), update: vi.fn(), amend: vi.fn(), resolve: vi.fn() }))
vi.mock('./transfersApi', async (orig) => {
  const real = await orig<typeof import('./transfersApi')>()
  return { ...real, transfersClient: { ...real.transfersClient, list: api.list, meta: async () => meta },
    fetchTransfer: api.fetch, createTransfer: api.create, cancelTransfer: api.cancel, readyTransfer: api.ready, updateTransfer: api.update,
    amendTransfer: api.amend, resolveTransfer: api.resolve }
})
const files = vi.hoisted(() => ({ list: vi.fn(), attach: vi.fn(), remove: vi.fn() }))
vi.mock('@/features/common/recordFiles', async (orig) => {
  const real = await orig<typeof import('@/features/common/recordFiles')>()
  return { ...real, listAttachments: files.list, attachFile: files.attach, removeAttachment: files.remove }
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
    Object.values(files).forEach((f) => f.mockReset())
    api.list.mockResolvedValue({ items: [t104, draft], total: 2 })
    files.list.mockResolvedValue([{ id: 30, kind: 'ATTACHMENT', direction: 'IN', remote_name: 'BOL signed.pdf', created_at: '2026-10-08T12:00:00Z',
      file_status: null, ack_status: null, st02: null, sent_at: null, error: null, ack_detail: null, size: 2048, created_by_name: 'Op', note: 'signed BOL' }])
  })

  it('like an order: the list sums what was sent and received; the sheet has its files, attachments and activity', async () => {
    api.fetch.mockResolvedValue(detail)
    renderAs(['sales.read', 'sales.transfer.manage'])
    const row = (await screen.findByText('00104')).closest('tr')!
    expect(row).toHaveTextContent('15,600')
    expect(row).toHaveTextContent('CF0092, CF0093')
    fireEvent.click(within(row).getByText('00104'))
    const dialog = await screen.findByRole('dialog')
    await openTab(dialog, 'Documents')
    expect(await within(dialog).findByRole('table', { name: 'Attachments' })).toHaveTextContent('BOL signed.pdf')
    expect(within(dialog).getByRole('table', { name: 'Attachments' })).toHaveTextContent('signed BOL')
    expect(within(dialog).getByRole('table', { name: 'Documents' })).toHaveTextContent('944-16468.edi')
    expect(within(dialog).queryByRole('table', { name: 'Documents' })).not.toHaveTextContent('BOL signed.pdf')
    expect(within(dialog).getByRole('button', { name: /Attach file/ })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Remove BOL signed.pdf' })).toBeInTheDocument()
    await openTab(dialog, 'Activity')
    expect(within(dialog).getByText('944 944-16468.edi (FR-OB-84)')).toBeInTheDocument()
  })

  it('a reader sees the attachments but cannot attach or remove; On Hold offers Release to a manager', async () => {
    api.fetch.mockResolvedValue({ ...detail, status: 'On Hold', status_before_hold: 'Discrepancy' })
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('00104'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('button', { name: /Release hold/ })).toBeNull()
    await openTab(dialog, 'Documents')
    expect(await within(dialog).findByRole('table', { name: 'Attachments' })).toHaveTextContent('BOL signed.pdf')
    expect(within(dialog).queryByRole('button', { name: /Attach file/ })).toBeNull()
    expect(within(dialog).queryByRole('button', { name: 'Remove BOL signed.pdf' })).toBeNull()
  })

  it('a discrepancy is settled: corrected to what arrived, with why', async () => {
    api.fetch.mockResolvedValue(detail)
    api.resolve.mockResolvedValue({ ...detail, status: 'Received', resolution: 'corrected', resolution_reason: 'our 943 was wrong', resolved_by_name: 'Op' })
    renderAs(['sales.read', 'sales.transfer.manage'])
    fireEvent.click(await screen.findByText('00104'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('button', { name: /Amend and send again/ })).toBeNull()  // a 944 has answered it
    fireEvent.click(await within(dialog).findByRole('button', { name: /Settle the difference/ }))
    expect(await within(dialog).findByText('line 2 CF0093: 0 of 7200 EA received')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('radio', { name: /correct it to what arrived/ }))
    fireEvent.change(within(dialog).getByRole('textbox', { name: /Why/ }), { target: { value: 'our 943 named CF0093; CF0103 was sent' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Settle the difference' }))
    await waitFor(() => expect(api.resolve).toHaveBeenCalledWith(1, 4, 'corrected', 'our 943 named CF0093; CF0103 was sent'))
  })

  it('a sent transfer with no 944 yet can be amended and sent again; a reader sees neither action', async () => {
    const waiting = { ...detail, status: 'Waiting for 944', receipts: [] }
    api.fetch.mockResolvedValue(waiting)
    renderAs(['sales.read', 'sales.transfer.manage'])
    fireEvent.click(await screen.findByText('00104'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('button', { name: /Settle the difference/ })).toBeNull()
    fireEvent.click(await within(dialog).findByRole('button', { name: /Amend and send again/ }))
    expect(await within(dialog).findByRole('textbox', { name: /What was wrong/ })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Amend and send again' })).toBeInTheDocument()
  })

  it('On Hold: Release asks why and goes back to where it was', async () => {
    const held = { ...detail, status: 'On Hold', status_before_hold: 'Discrepancy' }
    api.fetch.mockResolvedValue(held)
    renderAs(['sales.read', 'sales.transfer.manage'])
    fireEvent.click(await screen.findByText('00104'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByRole('button', { name: /Release hold/ }))
    expect(await screen.findByRole('dialog', { name: /Release transfer 00104/ })).toHaveTextContent('first 944 stays applied')
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
    fireEvent.keyDown(within(dialog).getByRole('button', { name: 'More actions' }), { key: 'Enter' })  // print only: the 943 is sent
    expect(await screen.findByRole('menuitem', { name: 'Transfer order (PDF)' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Cancel transfer' })).toBeNull()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
  })

  it('under each line, what the 944 received for it (pallet, lot, EA); what came off the transfer apart', async () => {
    api.fetch.mockResolvedValue(detail)
    renderAs(['sales.read', 'sales.transfer.manage'])
    fireEvent.click(await screen.findByText('00104'))
    const dialog = await screen.findByRole('dialog')
    await openTab(dialog, 'Lines')
    const lines = await within(dialog).findByRole('table', { name: 'Transfer lines' })
    fireEvent.click(within(within(lines).getByText('CF0092').closest('tr')!).getByRole('button', { name: 'Show details' }))
    const packed = await within(dialog).findByRole('table', { name: 'Received for this line' })
    expect(within(packed).getByText('26C253125110231').closest('tr')).toHaveTextContent('600')
    expect(within(dialog).getByText('Received but not on the transfer')).toBeInTheDocument()
    expect(within(dialog).getByText('26E248121010211')).toBeInTheDocument()  // CF0103: off the transfer
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
    fireEvent.keyDown(within(dialog).getByRole('button', { name: 'More actions' }), { key: 'Enter' })  // Cancel needs the permission
    expect(await screen.findByRole('menuitem', { name: 'Transfer order (PDF)' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Cancel transfer' })).toBeNull()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
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
