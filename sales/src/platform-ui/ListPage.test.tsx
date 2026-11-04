import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { ListPage, RecordSheet, ViewsProvider, queryKeys, type ColumnDef, type QuerySpec } from './index'
import { type ListMeta } from './resource/listMeta'
import { type ViewsClient } from './resource/viewsApi'

// ListPage (design §13.15): the one list implementation — its query keyed by
// the resource, the open row taken from the refreshed list, row actions, the
// detail sheet, and fixed filters without views.

type Row = { id: number; code: string; status: string }
const meta: ListMeta = { version: 1, searchable: true, default_sort: ['code'], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }

function makeResource(pages: Row[][]) {
  let call = 0
  return {
    basePath: '/things',
    meta: async () => meta,
    list: vi.fn(async (spec: QuerySpec) => ({ items: pages[Math.min(call++, pages.length - 1)], total: spec ? 1 : 0 })),
  }
}
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }, { accessorKey: 'status', header: 'Status' }]

function Refresh({ resource }: { resource: { basePath: string } }) {
  const qc = useQueryClient()
  return <button type="button" onClick={() => qc.invalidateQueries({ queryKey: queryKeys.list(resource) })}>refresh list</button>
}

function renderWith(node: React.ReactNode, withViews = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{withViews ? <ViewsProvider client={views}>{node}</ViewsProvider> : node}</QueryClientProvider>)
}

describe('ListPage', () => {
  it('opens a row in the detail sheet and keeps it current when the list refreshes', async () => {
    const resource = makeResource([[{ id: 1, code: 'A1', status: 'Review' }], [{ id: 1, code: 'A1', status: 'Pending' }]])
    renderWith(<>
      <ListPage<Row> resource={resource} title="Things" description="All of them" columns={columns}
        renderDetail={(r) => <RecordSheet title={`Thing ${r.code}`} tabs={[{ id: 'd', label: 'Detail', render: () => <p>detail says {r.status}</p> }]} />} />
      <Refresh resource={resource} />
    </>)
    expect(await screen.findByRole('heading', { name: 'Things' })).toBeInTheDocument()  // the page header
    fireEvent.click(await screen.findByText('A1'))
    const sheet = await screen.findByRole('dialog', { name: 'Thing A1' })
    expect(within(sheet).getByText('detail says Review')).toBeInTheDocument()
    fireEvent.click(screen.getByText('refresh list'))  // e.g. after a re-check: same key, derived from the resource
    expect(await within(sheet).findByText('detail says Pending')).toBeInTheDocument()  // the open row follows the list
  })

  it('row actions sit in their own column and never open the row', async () => {
    const act = vi.fn()
    const resource = makeResource([[{ id: 1, code: 'A1', status: 'Review' }, { id: 2, code: 'B2', status: 'Pending' }]])
    renderWith(<ListPage<Row> resource={resource} columns={columns} renderDetail={() => <RecordSheet title="Opened" tabs={[{ id: 'd', label: 'Detail', render: () => <p>opened</p> }]} />}
      rowActions={(r) => (r.status === 'Review' ? <button type="button" onClick={() => act(r.id)}>Fix</button> : null)} />)
    const row = (await screen.findByText('A1')).closest('tr')!
    expect(within(screen.getByText('B2').closest('tr')!).queryByText('Fix')).toBeNull()
    fireEvent.click(within(row).getByText('Fix'))
    expect(act).toHaveBeenCalledWith(1)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('columnheader', { name: /Actions/ })).toBeInTheDocument()
  })

  it('fixed filters are always sent and turn views off', async () => {
    const resource = makeResource([[{ id: 9, code: 'L9', status: 'ERROR' }]])
    renderWith(<ListPage<Row> resource={resource} columns={columns} fixed={[{ field: 'run_id', op: 'eq', value: '5' }]} />)
    await screen.findByText('L9')
    expect(resource.list).toHaveBeenCalledTimes(1)
    expect(resource.list.mock.calls[0][0].filters).toEqual([{ field: 'run_id', op: 'eq', value: '5' }])
    expect(screen.queryByRole('button', { name: /^View: / })).toBeNull()  // no view picker inside a record
  })

  it('with views it waits for the opening view, then asks once', async () => {
    const resource = makeResource([[{ id: 1, code: 'A1', status: 'Review' }]])
    renderWith(<ListPage<Row> resource={resource} columns={columns} />)
    expect(await screen.findByRole('button', { name: 'View: All' })).toBeInTheDocument()
    await waitFor(() => expect(resource.list).toHaveBeenCalledTimes(1))
  })
})
