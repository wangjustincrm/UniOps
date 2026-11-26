import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ViewsProvider, queryKeys } from '@/platform-ui'
import type { ListMeta } from '@/platform-ui/resource/listMeta'
import type { ActionOut, ActionRunBody } from '@/platform-ui/resource/ResourceClient'
import type { ViewsClient } from '@/platform-ui/resource/viewsApi'
import type { InvoiceRead } from './invoicesApi'

// Send on the selected invoices (user 2026-11-26, future item 18): the selection bar, the dry run's words (how many go, which
// not and why, how each goes, what is sent), one result per invoice, and both lists read again.

const SENDS = "the invoice to the partner (an 810 or an email), and the company's copy"
const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }],
  capabilities: { export: null, suggest: false, actions: [{ id: 'send', allowed: true, label: 'Send', bulk: true, sends: SENDS },
    { id: 'send_again', allowed: true, label: 'Send again', bulk: false, sends: SENDS }] } }
const inv = (id: number, number: string, status: string): InvoiceRead => ({ id, version: id, invoice_number: number, status, order_id: 100 + id,
  method: '810_JSON', currency: 'CAD', total: '10.00', send_count: 0, sent_at: null } as unknown as InvoiceRead)
const ROWS = [inv(1, 'IN-1', 'Ready'), inv(2, 'IN-2', 'Ready'), inv(3, 'IN-3', 'Sent'), inv(4, 'IN-4', 'Blocked'), inv(5, 'IN-5', 'Ready')]
const OUTCOME: Record<number, ActionOut['results'][number]> = {
  1: { id: 1, status: 'done', note: 'an 810 file to TrueCommerce' }, 2: { id: 2, status: 'done', note: 'an email to ap@sobeys.example' },
  3: { id: 3, status: 'skipped', code: 'invoice.already_sent', reason: 'the invoice is sent already: send it again only on purpose, saying why' },
  4: { id: 4, status: 'skipped', code: 'invoice.not_sendable', reason: 'the invoice is Blocked' },
  5: { id: 5, status: 'done', note: 'an 810 file to TrueCommerce' },
}
const action = vi.fn(async (_id: string, body: ActionRunBody): Promise<ActionOut> => {
  const results = Object.keys(body.ids ?? {}).map((id) => (body.dry_run || Number(id) !== 5 ? OUTCOME[Number(id)]
    : { id: 5, status: 'failed' as const, code: 'entity.stale', reason: 'changed since you selected it: reload and look again' }))
  const n = (s: string) => results.filter((r) => r.status === s).length
  return { action: 'send', dry_run: !!body.dry_run, results, counts: { done: n('done'), skipped: n('skipped'), failed: n('failed') } }
})
vi.mock('./invoicesApi', async (orig) => {
  const real = await orig<typeof import('./invoicesApi')>()
  return { ...real, invoicesClient: { ...real.invoicesClient, meta: async () => meta, list: async () => ({ items: ROWS, total: ROWS.length }),
    action: (id: string, body: ActionRunBody) => action(id, body) } }
})
const { default: InvoicesPage } = await import('./InvoicesPage')
const { invoicesClient } = await import('./invoicesApi')
const { ordersClient } = await import('@/features/orders/ordersApi')
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }

function mount(keys = ['sales.read', 'sales.invoice.manage']) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const spy = vi.spyOn(qc, 'invalidateQueries')
  render(<QueryClientProvider client={qc}><ViewsProvider client={views}><PermissionProvider can={(k) => keys.includes(k)}>
    <InvoicesPage /></PermissionProvider></ViewsProvider></QueryClientProvider>)
  return spy
}

describe('Send on the selected invoices', () => {
  it('without sales.invoice.manage: no Send in a bar (nor checkboxes)', async () => {
    mount(['sales.read'])
    await screen.findByText('IN-1')
    expect(screen.queryByRole('checkbox')).toBeNull()
  })

  it('the bar counts what fits; the dry run says how many go, which not and why, how each goes and what is sent; then one result each', async () => {
    const invalidated = mount()
    await screen.findByText('IN-1')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all on this page' }))
    const bar = screen.getByRole('toolbar', { name: 'Selected records' })
    fireEvent.click(within(bar).getByRole('button', { name: 'Send (3 of 5)' }))        // Ready ones fit; Sent and Blocked do not
    const dialog = await screen.findByRole('dialog', { name: 'Send 5 invoices' })
    expect(await within(dialog).findByText('3 will be sent')).toBeInTheDocument()
    expect(within(dialog).getByText('IN-3: the invoice is sent already: send it again only on purpose, saying why')).toBeInTheDocument()
    expect(within(dialog).getByText('IN-4: the invoice is Blocked')).toBeInTheDocument()
    const how = within(dialog).getByRole('list', { name: 'How each goes' })
    expect(how).toHaveTextContent('2 by an 810 file to TrueCommerce: IN-1, IN-5')
    expect(how).toHaveTextContent('1 by an email to ap@sobeys.example: IN-2')
    expect(dialog).toHaveTextContent(`Sends ${SENDS}.`)
    expect(action).toHaveBeenCalledWith('send', { ids: { 1: 1, 2: 2, 3: 3, 4: 4, 5: 5 }, dry_run: true })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send (3)' }))
    await waitFor(() => expect(action).toHaveBeenLastCalledWith('send', { ids: { 1: 1, 2: 2, 3: 3, 4: 4, 5: 5 }, input: {} }))
    const said = await screen.findByText(/2 queued to send · 2 skipped · 1 failed/)
    expect(said).toHaveTextContent('IN-5: changed since you selected it')                  // one per invoice: the failed one named
    expect(said).toHaveTextContent('IN-1: an 810 file to TrueCommerce')
    expect(said).toHaveTextContent('IN-2: an email to ap@sobeys.example')
    for (const key of [queryKeys.list(ordersClient), queryKeys.details(ordersClient), queryKeys.list(invoicesClient), queryKeys.details(invoicesClient)]) {
      expect(invalidated).toHaveBeenCalledWith({ queryKey: key })                     // the orders it moved are read again too
    }
  })
})
