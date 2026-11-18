import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { ListPage, ViewsProvider, type ColumnDef, type QuerySpec } from './index'
import { type FieldMeta, type ListMeta } from './resource/listMeta'
import { type ViewsClient } from './resource/viewsApi'

// The CSV follows the view (user 2026-11-18): Export CSV offers "Visible columns" — the columns shown, in their
// order, only those each row carries (a page's own computed cell is not a field) — and "All fields", the list's
// full export.

const f = (name: string, label: string, in_row = true): FieldMeta => ({
  name, label, type: 'text', enum_values: [], filterable: true, sortable: true, groupable: false, operators: ['eq'], in_row })
const meta: ListMeta = { version: 1, searchable: false, default_sort: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }],
  fields: [f('code', 'Code'), f('status', 'Status'), f('net', 'Net'), f('refs', 'Refs', false)] }
type Row = { id: number; code: string; status: string; net: string }
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
const columns: ColumnDef<Row, unknown>[] = [
  { accessorKey: 'status', header: 'Status' }, { accessorKey: 'code', header: 'Code' }, { id: 'place', header: 'Place', cell: () => 'x' }]

describe('Export CSV', () => {
  it('Visible columns: the shown fields in their order; All fields: no column list', async () => {
    const exportPath = vi.fn((_spec: QuerySpec, _file?: string, cols?: string[]) => `/things/export.csv?${cols ? `columns=${cols.join(',')}` : 'all'}`)
    const onExport = vi.fn(async () => undefined)
    render(<QueryClientProvider client={new QueryClient()}><ViewsProvider client={views}>
      <ListPage<Row> title="Things" columns={columns} onExport={onExport}
        resource={{ basePath: '/things', meta: async () => meta, list: async () => ({ items: [{ id: 1, code: 'A', status: 'Open', net: '1' }], total: 1 }), exportPath }} />
    </ViewsProvider></QueryClientProvider>)
    await screen.findByText('Open')
    fireEvent.keyDown(screen.getByRole('button', { name: 'Export CSV' }), { key: 'Enter' })
    fireEvent.click(await screen.findByText('Visible columns (2)'))
    await waitFor(() => expect(onExport).toHaveBeenCalledWith('/things/export.csv?columns=status,code'))  // "Place" is the page's own cell
    fireEvent.keyDown(screen.getByRole('button', { name: 'Export CSV' }), { key: 'Enter' })
    fireEvent.click(await screen.findByText('All fields'))
    await waitFor(() => expect(onExport).toHaveBeenLastCalledWith('/things/export.csv?all'))
  })
})
