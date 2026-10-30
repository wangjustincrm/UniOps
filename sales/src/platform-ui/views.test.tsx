import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import {
  QueryTable, ViewsProvider, useListView, type ColumnDef, type ListMeta, type SavedView, type ViewState, type ViewsClient,
} from './index'

// Saved views and column order (design §13.19): the picker's actions and the
// order of the columns going into the saved state.

type Row = { id: number; code: string; name: string; price: string }
const meta: ListMeta = {
  version: 1, searchable: true, default_sort: ['code'],
  fields: ['code', 'name', 'price'].map((n) => ({ name: n, label: n[0].toUpperCase() + n.slice(1), type: 'text' as const, enum_values: [],
    filterable: true, sortable: true, groupable: false, operators: ['contains' as const] })),
  views: [{ key: 'all', name: 'All', state: { version: 1 } }],
}
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }, { accessorKey: 'name', header: 'Name' }, { accessorKey: 'price', header: 'Price' }]
const resource = { basePath: '/things', meta: async () => meta }
const base: ViewState = { version: 1, sort: [], group: [], filters: [], pageSize: 50, columns: [], hidden: [] }

function fakeViews(mine: SavedView[]): ViewsClient & { rows: SavedView[] } {
  const rows = mine.map((m) => ({ ...m }))
  return {
    rows,
    list: vi.fn(async () => rows.map((r) => ({ ...r }))),
    create: vi.fn(),
    update: vi.fn(async (id, data) => { const v = rows.find((r) => r.id === id)!; Object.assign(v, data, { version: v.version + 1 }); return { ...v } }),
    remove: vi.fn(async (id) => { rows.splice(rows.findIndex((r) => r.id === id), 1) }),
    setDefault: vi.fn(async (id, on) => { rows.forEach((r) => { r.is_default = on && r.id === id }); return { ...rows.find((r) => r.id === id)! } }),
  }
}

function List() {
  const view = useListView(resource)
  return (
    <QueryTable<Row> columns={columns} rows={[{ id: 1, code: 'A1', name: 'Apple', price: '3' }]} total={1} {...view.tableProps}
      resource={resource} rowKey={(r) => r.id} searchable />
  )
}

function renderList(views: ViewsClient) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ViewsProvider client={views}><List /></ViewsProvider></QueryClientProvider>)
}
const headers = () => screen.getAllByRole('columnheader').map((h) => h.textContent?.trim()).filter(Boolean)
// Like a user: a dialog or menu has gone and the focus is back before the next step.
const settle = async () => {
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  await act(() => new Promise((r) => setTimeout(r, 0)))
}
const openPicker = async (name: string) => {
  await settle()
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(`^View: ${name}`) }))
}
const moreOf = async (name: string) => {
  const mine = await screen.findByRole('group', { name: 'My views' })
  fireEvent.keyDown(within(mine).getByRole('button', { name: `${name}: more` }), { key: 'Enter' })
  return screen.findByRole('menu')
}

describe('view picker', () => {
  const saved: SavedView = { id: 3, resource: 'things', name: 'Cheap', state: { ...base, filters: [{ field: 'price', op: 'contains', value: '1' }] },
    is_default: false, sort: 0, version: 1, stale: false }

  it('Save updates my view; Rename, Set as default and Delete act on it', async () => {
    const views = fakeViews([saved])
    renderList(views)
    await openPicker('All')
    fireEvent.click(within(await screen.findByRole('group', { name: 'My views' })).getByText('Cheap'))
    expect(await screen.findByRole('button', { name: 'View: Cheap' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'x' } })
    fireEvent.keyDown(screen.getByLabelText('Search'), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('button', { name: 'Save' }))
    await waitFor(() => expect(views.update).toHaveBeenCalledWith(3, { state: expect.objectContaining({ search: 'x' }) }, 1))
    expect(await screen.findByRole('button', { name: 'View: Cheap' })).toBeInTheDocument()  // saved: no longer unsaved

    await openPicker('Cheap')
    fireEvent.click(within(await moreOf('Cheap')).getByText('Rename'))
    const dialog = await screen.findByRole('dialog', { name: 'Rename the view' })
    fireEvent.change(within(dialog).getByLabelText('View name'), { target: { value: 'Under 10' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(views.update).toHaveBeenLastCalledWith(3, { name: 'Under 10' }))
    expect(await screen.findByRole('button', { name: 'View: Under 10' })).toBeInTheDocument()

    await openPicker('Under 10')
    fireEvent.click(within(await moreOf('Under 10')).getByText('Set as default'))
    await waitFor(() => expect(views.setDefault).toHaveBeenCalledWith(3, true))
    // The picker stays open: the star shows which view opens the list.
    expect(await within(screen.getByRole('group', { name: 'My views' })).findByLabelText('default')).toBeInTheDocument()

    fireEvent.click(within(await moreOf('Under 10')).getByText('Delete'))
    const confirm = await screen.findByRole('dialog', { name: /Delete the view "Under 10"/ })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(views.remove).toHaveBeenCalledWith(3))
    expect(await screen.findByRole('button', { name: 'View: All' })).toBeInTheDocument()  // back to the first system view
  })

  it('a view saved by an older version says so', async () => {
    renderList(fakeViews([{ ...saved, is_default: true, stale: true }]))
    expect(await screen.findByRole('button', { name: 'View: Cheap' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Saved by an older version: review it, then Save.')
  })
})

describe('column order', () => {
  it('↑ ↓ in the column chooser move a column; the order is part of the view', async () => {
    const views = fakeViews([{ id: 3, resource: 'things', name: 'Mine', state: base, is_default: true, sort: 0, version: 1, stale: false }])
    renderList(views)
    await screen.findByRole('button', { name: 'View: Mine' })
    expect(headers()).toEqual(['Code', 'Name', 'Price'])
    fireEvent.click(screen.getByRole('button', { name: 'Show, hide or move columns' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Move Price up' }))
    await waitFor(() => expect(headers()).toEqual(['Code', 'Price', 'Name']))
    expect(screen.getByRole('button', { name: 'Move Code up' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(views.update).toHaveBeenCalledWith(3, { state: expect.objectContaining({ columns: ['code', 'price', 'name'] }) }, 1))
  })

  it('a header dragged onto another goes before it', async () => {
    renderList(fakeViews([]))
    await screen.findByRole('button', { name: 'View: All' })
    const th = (name: string) => screen.getByRole('columnheader', { name: new RegExp(`^${name}`) })
    const data = new Map<string, string>()
    const dataTransfer = { setData: (k: string, v: string) => data.set(k, v), getData: (k: string) => data.get(k) ?? '', effectAllowed: '' }
    const grip = th('Price').querySelector('[data-column-grip]')!
    expect(grip).toHaveAttribute('draggable', 'true')
    fireEvent.dragStart(grip, { dataTransfer })
    fireEvent.dragOver(th('Code'), { dataTransfer })
    fireEvent.drop(th('Code'), { dataTransfer })
    await waitFor(() => expect(headers()).toEqual(['Price', 'Code', 'Name']))
    expect(await screen.findByRole('button', { name: 'View: All (unsaved changes)' })).toBeInTheDocument()
  })
})
