import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { ListPage, ViewsProvider, type ColumnDef, type QuerySpec } from './index'
import { type ListPageProps } from './patterns/ListPage'
import { type FieldMeta, type ListMeta } from './resource/listMeta'
import { type ViewsClient } from './resource/viewsApi'

// The CSV follows the view (user 2026-11-18): Export CSV offers "Visible columns" — the columns shown, in their
// order, those each row carries and the page's own the server computes — and "All fields", the list's full export. A
// shown column a file cannot carry is named. The server says who may (user 2026-11-24: /meta capabilities).

const f = (name: string, label: string, in_row = true): FieldMeta => ({
  name, label, type: 'text', enum_values: [], filterable: true, sortable: true, groupable: false, operators: ['eq'], in_row })
const meta: ListMeta = { version: 1, searchable: false, default_sort: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }],
  fields: [f('code', 'Code'), f('status', 'Status'), f('net', 'Net'), f('refs', 'Refs', false)] }
type Row = { id: number; code: string; status: string; net: string }
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
const columns: ColumnDef<Row, unknown>[] = [
  { accessorKey: 'status', header: 'Status' }, { accessorKey: 'code', header: 'Code' }, { id: 'place', header: 'Place', cell: () => 'x' }]

const caps = (computed: { id: string; label: string }[] = [], allowed = true): ListMeta => ({ ...meta, capabilities: { export: { allowed, computed }, suggest: false, actions: [] } })
const exportCsv = vi.fn<(spec: QuerySpec, columns?: string[]) => Promise<void>>(async () => undefined)
const show = (m: ListMeta, props: Partial<ListPageProps<Row>> = {}) => {
  exportCsv.mockClear()
  return render(<QueryClientProvider client={new QueryClient()}><ViewsProvider client={views}>
    <ListPage<Row> title="Things" columns={columns} {...props}
      resource={{ basePath: '/things', meta: async () => m, list: async () => ({ items: [{ id: 1, code: 'A', status: 'Open', net: '1' }], total: 1 }), exportCsv }} />
  </ViewsProvider></QueryClientProvider>)
}
const open = async () => { await screen.findByText('Open'); fireEvent.keyDown(await screen.findByRole('button', { name: 'Export CSV' }), { key: 'Enter' }) }

describe('Export CSV', () => {
  it('Visible columns: the shown fields in their order; All fields: no column list; what is left out is named', async () => {
    show(caps())
    await open()
    expect(await screen.findByText('Not in the file: Place')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Visible columns (2)'))
    await waitFor(() => expect(exportCsv).toHaveBeenCalledWith(expect.anything(), ['status', 'code']))
    expect(await screen.findByText('CSV exported, without Place')).toBeInTheDocument()
    await open()
    fireEvent.click(await screen.findByText('All fields'))
    await waitFor(() => expect(exportCsv).toHaveBeenLastCalledWith(expect.anything(), undefined))
  })

  it("the page's own column the server computes is in the file, and the row's controls are never named", async () => {
    show(caps([{ id: 'place', label: 'Place' }]), { rowActions: () => 'x' })
    await open()
    fireEvent.click(await screen.findByText('Visible columns (3)'))
    await waitFor(() => expect(exportCsv).toHaveBeenCalledWith(expect.anything(), ['status', 'code', 'place']))
    expect(screen.queryByText(/Not in the file/)).toBeNull()
  })

  it.each([
    ['the server offers none', { ...meta, capabilities: { export: null, suggest: false, actions: [] } }, {}],
    ['this person may not', caps([], false), {}],
    ['an older server says nothing', meta, {}],
    ['a part of a page (no title)', caps(), { title: undefined }],
    ['the page turns it off', caps(), { exportable: false }],
  ])('no button when %s', async (_why, m, props) => {
    show(m as ListMeta, props)
    await screen.findByText('Open')
    expect(screen.queryByRole('button', { name: 'Export CSV' })).toBeNull()
  })

  it('a part of a page that asks for it has the button', async () => {
    show(caps(), { title: undefined, exportable: true })
    expect(await screen.findByRole('button', { name: 'Export CSV' })).toBeInTheDocument()
  })
})
