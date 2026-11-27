import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ListPage, PermissionProvider, ViewsProvider, defineActions, type ColumnDef, type QuerySpec } from './index'
import type { FieldMeta, ListMeta } from './resource/listMeta'
import type { ViewsClient } from './resource/viewsApi'
import { toSearchParams } from './resource/querySpec'

// A record's own list as a scope (user 2026-11-27: an item's Orders): locked, shown apart from Search and never in the
// search panel; it rides in every read and count (?scope=), and with the chosen records of an action, so the server
// enforces it; its views are its kind's ("orders@item"), not the list's own.

type Row = { id: number; version: number; code: string }
const OPS: FieldMeta['operators'] = ['eq', 'in', 'contains']
const f = (name: string): FieldMeta => ({ name, label: name, type: 'text', enum_values: [], filterable: true, sortable: true, groupable: false, operators: OPS })
const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [f('code'), f('item_code'), f('partner')], views: [
  { key: 'open', name: 'Open', state: { version: 1, filters: [{ field: 'partner', op: 'eq', value: 'X' }] } },  // the list's default
  { key: 'all', name: 'All', state: { version: 1 } }] }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }]
const ACTIONS = defineActions<Row>([{ id: 'touch', label: 'Touch', permission: 'app.edit', scope: 'group', placement: ['row', 'bulk'],
  will: (n) => `${n} will be touched`, done: (n) => `${n} touched` }])
const scope = { kind: 'item', label: 'Item = CF0093', filters: [{ field: 'item_code', op: 'eq' as const, value: 'CF0093' }] }

function mount() {
  const list = vi.fn(async (...args: [QuerySpec]) => args &&  ({ items: [{ id: 1, version: 2, code: 'A-1' }], total: 1 }))
  const count = vi.fn(async (...args: [QuerySpec]) => args && { total: 1 })
  const action = vi.fn(async () => ({ action: 'touch', dry_run: true, results: [{ id: 1, status: 'done' as const }], counts: { done: 1, skipped: 0, failed: 0 } }))
  const views: ViewsClient = { list: vi.fn(async () => []), create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><PermissionProvider can={() => true}><ViewsProvider client={views}>
    <ListPage<Row> resource={{ basePath: '/orders', meta: async () => meta, list, count, action }} columns={columns} scope={scope} actions={ACTIONS} />
  </ViewsProvider></PermissionProvider></QueryClientProvider>)
  return { list, count, action, views }
}

describe('a scope', () => {
  it('rides in every read; shown as a lock, not a removable search, and not in the search panel', async () => {
    const t = mount()
    await screen.findByText('A-1')
    const spec = t.list.mock.calls.at(-1)![0]
    expect(spec.scope).toEqual(scope.filters)
    expect(toSearchParams(spec).getAll('scope')).toEqual(['item_code:eq:CF0093'])
    expect(spec.filters ?? []).toEqual([])  // never a search the person could clear
    expect(screen.getByRole('note', { name: 'Scope: Item = CF0093' })).toHaveTextContent('Scope: Item = CF0093')
    fireEvent.click(screen.getByRole('button', { name: /^Search on any field/ }))
    const panel = await screen.findByRole('dialog', { name: 'Search on any field' })
    expect(within(panel).queryByRole('button', { name: 'Add item_code' })).toBeNull()
    expect(within(panel).getByRole('button', { name: 'Add partner' })).toBeInTheDocument()
  })

  it('its views are the scope kind\'s ("orders@item"), never the list\'s own', async () => {
    const t = mount()
    await screen.findByText('A-1')
    await waitFor(() => expect(t.views.list).toHaveBeenCalledWith('orders@item'))
    expect(t.views.list).not.toHaveBeenCalledWith('orders')
  })

  it('an action on chosen records carries the scope, for the server to refuse any outside it', async () => {
    const t = mount()
    await screen.findByText('A-1')
    fireEvent.click(screen.getByRole('button', { name: 'Actions for #1' }))
    fireEvent.keyDown(screen.getByRole('button', { name: 'Actions for #1' }), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Touch' }))
    await waitFor(() => expect(t.action).toHaveBeenCalled())
    expect(t.action.mock.calls[0]).toEqual(['touch', expect.objectContaining({ ids: { 1: 2 }, scope: ['item_code:eq:CF0093'] })])
  })

  it('opens on All, not the list\'s default view: the rows are the scope\'s, as the record\'s tab counts them', async () => {
    const t = mount()
    await screen.findByText('A-1')
    expect(t.list.mock.calls.every(([s]) => (s.filters ?? []).length === 0)).toBe(true)  // never the Open view's filter
    expect(screen.getByRole('button', { name: /^View/ })).toHaveTextContent('All')
  })

  it('the lock is first on the left, before the views and Search', async () => {
    mount()
    await screen.findByText('A-1')
    const lock = screen.getByRole('note', { name: 'Scope: Item = CF0093' })
    const search = screen.getByLabelText('Search')
    expect(lock.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
