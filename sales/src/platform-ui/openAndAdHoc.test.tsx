import { describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ListPage } from './patterns/ListPage'
import { RecordSheet } from './patterns/RecordSheet'
import { ViewsProvider } from './patterns/ViewsProvider'
import { requestOpen, requestView } from './resource/viewRequests'
import type { ListMeta } from './resource/listMeta'
import type { ViewsClient } from './resource/viewsApi'

// A record asked for from elsewhere (a Task Inbox link) opens in the list's
// sheet with its own data; a view made on the spot (a dashboard card) opens
// the list with its filters, under its name.

type Rec = { id: number; name: string }
const meta: ListMeta = { version: 1, searchable: false, default_sort: [],
  fields: [{ name: 'name', label: 'Name', type: 'text', enum_values: [], filterable: true, sortable: true, groupable: false, operators: ['eq'] }],
  views: [{ key: 'all', name: 'All', state: { version: 1 } }] }
const views: ViewsClient = { list: vi.fn(async () => []), create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }

function setup() {
  const resource = {
    basePath: '/things', meta: async () => meta,
    list: vi.fn(async () => ({ items: [{ id: 1, name: 'first' }], total: 1 })),
    get: vi.fn(async (id: number) => ({ id, name: `number ${id}` })),
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
    <ListPage<Rec> resource={resource} title="Things" columns={[{ accessorKey: 'name', header: 'Name' }]}
      renderDetail={(r) => <RecordSheet title={`Thing ${r.name}`} tabs={[{ id: 'd', label: 'Detail', render: () => <p>detail of {r.name}</p> }]} />} />
  </ViewsProvider></QueryClientProvider>)
  return resource
}

describe('a list opened from elsewhere', () => {
  it('opens the requested record, fetched by its id, even if it is not on the page', async () => {
    requestOpen('/things', 42)  // before the list exists (a fresh tab)
    const r = setup()
    const sheet = await screen.findByRole('dialog', { name: 'Thing number 42' })
    expect(within(sheet).getByText('detail of number 42')).toBeInTheDocument()
    expect(r.get).toHaveBeenCalledWith(42)
  })

  it('applies a view made on the spot: its filters, under its name', async () => {
    const r = setup()
    await screen.findByText('first')
    act(() => requestView('things', { name: 'On Hold', state: { filters: [{ field: 'name', op: 'eq', value: 'x' }] } }))
    await waitFor(() => expect(r.list).toHaveBeenLastCalledWith(expect.objectContaining({
      filters: [{ field: 'name', op: 'eq', value: 'x' }] })))
    expect(screen.getByRole('button', { name: 'View: On Hold' })).toBeInTheDocument()
  })
})
