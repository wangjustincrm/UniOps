import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import {
  QueryTable, flipSort, parseView, removeSort, serializeView, setFieldFilters, sortBy, toSearchParams, viewStateOf,
  type ColumnDef, type ListMeta, type QuerySpec,
} from './index'
import { chooseOption } from './testing'

// The list query contract on the client (design §13.19): the column menu,
// the chips, the search panel and the column chooser edit one QuerySpec, and
// the request is serialised from it.

const meta: ListMeta = {
  version: 1, searchable: true, default_sort: ['code'],
  fields: [
    { name: 'code', label: 'Item code', type: 'text', enum_values: [], filterable: true, sortable: true, groupable: false,
      operators: ['contains', 'not_contains', 'starts_with', 'ends_with', 'eq', 'ne', 'in', 'not_in', 'is_null', 'not_null'] },
    { name: 'price', label: 'Price', type: 'number', enum_values: [], filterable: true, sortable: true, groupable: false,
      operators: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in', 'is_null', 'not_null'] },
    { name: 'status', label: 'Status', type: 'enum', enum_values: ['Pending', 'Review', 'Shipped'], filterable: true, sortable: false,
      groupable: false, operators: ['eq', 'ne', 'in', 'not_in', 'is_null', 'not_null'] },
    { name: 'sku', label: 'SKU', type: 'text', enum_values: [], filterable: true, sortable: true, groupable: false, operators: ['contains', 'eq'] },
  ],
}
type Row = { id: number; code: string; price: string; status: string; sku: string }
const rows: Row[] = [{ id: 1, code: 'CF0092', price: '9.50', status: 'Review', sku: 'SKU-92' }]
const columns: ColumnDef<Row, unknown>[] = [
  { accessorKey: 'code', header: 'Code' }, { accessorKey: 'price', header: 'Price' }, { accessorKey: 'status', header: 'Status' },
]
const resource = { basePath: '/things', meta: async () => meta }

function Harness({ initial, locked, onSpec }: { initial?: QuerySpec; locked?: string[]; onSpec: (s: QuerySpec) => void }) {
  const [spec, setSpec] = useState<QuerySpec>(initial ?? { page: 3, pageSize: 50 })
  return (
    <QueryTable<Row> columns={columns} rows={rows} total={1} spec={spec} resource={resource} locked={locked} searchable
      rowKey={(r) => r.id} onSpecChange={(s) => { setSpec(s); onSpec(s) }} />
  )
}

function renderTable(props: Partial<Parameters<typeof Harness>[0]> = {}) {
  const onSpec = vi.fn()
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><Harness onSpec={onSpec} {...props} /></QueryClientProvider>)
  const last = (): QuerySpec => onSpec.mock.calls.at(-1)![0]
  return { onSpec, last, request: () => toSearchParams(last()) }
}

const openMenu = async (label: string) => {
  fireEvent.keyDown(await screen.findByRole('button', { name: `${label} column menu` }), { key: 'Enter' })
  return screen.findByRole('menu')
}

describe('query spec helpers', () => {
  it('serialises several values, escapes, between and empty checks', () => {
    const p = toSearchParams({ page: 1, pageSize: 50, filters: [
      { field: 'code', op: 'contains', value: ['a|b', 'c\\d'] }, { field: 'price', op: 'between', value: ['1', null] },
      { field: 'sku', op: 'is_null', value: null }, { field: 'status', op: 'not_in', value: ['Review'] }] })
    expect(p.getAll('filter')).toEqual(['code:contains:a\\|b|c\\\\d', 'price:between:1..', 'sku:is_null:', 'status:not_in:Review'])
  })
  it('multi-sort keeps the order as priority; one field’s filters are replaced together', () => {
    let s: QuerySpec = { page: 4, pageSize: 50 }
    s = sortBy(sortBy(s, 'code', false), 'price', true)
    expect(s.sort).toEqual([{ field: 'code', desc: false }, { field: 'price', desc: true }])
    s = sortBy(s, 'code', true)  // already there: keeps its place
    expect(s.sort?.map((k) => [k.field, k.desc])).toEqual([['code', true], ['price', true]])
    expect(flipSort(s, 'price').sort?.[1].desc).toBe(false)
    expect(removeSort(s, 'code').sort).toEqual([{ field: 'price', desc: true }])
    s = setFieldFilters({ ...s, filters: [{ field: 'code', op: 'eq', value: 'x' }, { field: 'price', op: 'gt', value: '1' }] },
      'code', [{ field: 'code', op: 'contains', value: 'y' }])
    expect([s.page, s.filters]).toEqual([1, [{ field: 'price', op: 'gt', value: '1' }, { field: 'code', op: 'contains', value: 'y' }]])
  })
  it('a view state is versioned JSON', () => {
    const v = viewStateOf({ page: 2, pageSize: 25, sort: [{ field: 'code' }], search: 'x' }, { columns: ['code', 'price'], hidden: ['price'] })
    expect(parseView(serializeView(v))).toEqual(v)
    expect(parseView(JSON.stringify({ ...v, version: 2 }))).toBeNull()
    expect(parseView('not json')).toBeNull()
  })
})

describe('QueryTable with /meta', () => {
  it('the column menu sorts (multi-sort) and the request follows', async () => {
    const t = renderTable()
    fireEvent.click(within(await openMenu('Item code')).getByText('Sort descending'))
    expect(t.last().sort).toEqual([{ field: 'code', desc: true }])
    expect(t.last().page).toBe(1)
    fireEvent.click(within(await openMenu('Price')).getByText('Sort ascending'))
    expect(t.request().get('sort')).toBe('-code,price')
  })

  it('a field that cannot be sorted or filtered offers only what it can', async () => {
    renderTable()
    const menu = await openMenu('Status')
    expect(within(menu).queryByText('Sort ascending')).toBeNull()
    expect(within(menu).getByText('Search')).toBeInTheDocument()
  })

  it('chips show sort and search; a click flips, × removes; a locked filter stays', async () => {
    const t = renderTable({ locked: ['status'], initial: { page: 2, pageSize: 50, search: 'milk',
      sort: [{ field: 'code' }, { field: 'price', desc: true }],
      filters: [{ field: 'status', op: 'eq', value: 'Review' }, { field: 'code', op: 'contains', value: ['l', 'm'] }] } })
    const chips = await screen.findByRole('group', { name: 'Active sort and search' })
    expect(within(chips).getByText('Sorting')).toBeInTheDocument()
    expect(within(chips).getByText('Item code contains "l" or "m"')).toBeInTheDocument()
    expect(within(chips).getByText('Status is "Review"')).toBeInTheDocument()
    expect(within(chips).queryByRole('button', { name: /Remove Status/ })).toBeNull()  // locked
    fireEvent.click(within(chips).getByText('Item code'))
    expect(t.last().sort).toEqual([{ field: 'code', desc: true }, { field: 'price', desc: true }])
    fireEvent.click(within(chips).getByRole('button', { name: 'Remove sort by Price' }))
    expect(t.last().sort).toEqual([{ field: 'code', desc: true }])
    fireEvent.click(within(chips).getByRole('button', { name: 'Remove Item code contains "l" or "m"' }))
    expect(t.last().filters).toEqual([{ field: 'status', op: 'eq', value: 'Review' }])
    fireEvent.click(within(chips).getByRole('button', { name: 'Clear the quick search' }))
    expect(t.last().search).toBeUndefined()
  })

  it('the search panel builds the query: values of a field OR, fields AND', async () => {
    const t = renderTable({ locked: ['status'] })
    fireEvent.click(within(await openMenu('Item code')).getByText('Search'))  // opens on that field, ticked
    const panel = await screen.findByRole('dialog', { name: 'Search' })
    expect(within(panel).queryByText('Status')).toBeNull()  // locked by the page
    await chooseOption(within(panel).getByLabelText('Item code: how to compare'), 'starts with')
    const code = within(panel).getByLabelText('Item code: value')
    fireEvent.change(code, { target: { value: 'CF' } })
    fireEvent.keyDown(code, { key: 'Enter' })
    fireEvent.change(code, { target: { value: 'S0' } })  // typed, not yet added: Search adds it
    fireEvent.click(within(panel).getByLabelText('Price'))
    await chooseOption(within(panel).getByLabelText('Price: how to compare'), 'between')
    fireEvent.change(within(panel).getByLabelText('Price: from'), { target: { value: '1' } })
    fireEvent.change(within(panel).getByLabelText('Price: to'), { target: { value: '10' } })
    fireEvent.click(within(panel).getByRole('button', { name: 'Search' }))
    expect(t.request().getAll('filter')).toEqual(['code:starts_with:CF|S0', 'price:between:1..10'])
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Search' })).toBeNull())
  })

  it('an enum field is chosen from its values; Clear resets the panel', async () => {
    const t = renderTable()
    fireEvent.click(await screen.findByRole('button', { name: 'Search' }))
    const panel = await screen.findByRole('dialog', { name: 'Search' })
    fireEvent.click(within(panel).getByLabelText('Status'))
    const values = within(panel).getByRole('group', { name: 'Status: value' })
    fireEvent.click(within(values).getByLabelText('Pending'))
    fireEvent.click(within(values).getByLabelText('Review'))
    await chooseOption(within(panel).getByLabelText('Status: how to compare'), 'is not')
    fireEvent.click(within(panel).getByRole('button', { name: 'Search' }))
    expect(t.request().getAll('filter')).toEqual(['status:not_in:Pending|Review'])
    fireEvent.click(await screen.findByRole('button', { name: 'Search' }))
    const again = await screen.findByRole('dialog', { name: 'Search' })
    expect(within(again).getByLabelText('Status')).toBeChecked()  // starts from the current filters
    fireEvent.click(within(again).getByText('Clear'))
    fireEvent.click(within(again).getByRole('button', { name: 'Search' }))
    expect(t.last().filters).toEqual([])
  })

  it('hide a column from its menu; the eye shows it again and any other field', async () => {
    renderTable()
    fireEvent.click(within(await openMenu('Price')).getByText('Hide column'))
    await waitFor(() => expect(screen.queryByRole('columnheader', { name: /Price/ })).toBeNull())
    expect(screen.queryByRole('columnheader', { name: /SKU/ })).toBeNull()  // not a page column: hidden at first
    fireEvent.click(screen.getByRole('button', { name: 'Show or hide columns' }))
    fireEvent.click(await screen.findByLabelText('Price'))
    fireEvent.click(screen.getByLabelText('SKU'))
    expect(await screen.findByRole('columnheader', { name: /Price/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /SKU/ })).toBeInTheDocument()
    expect(screen.getByText('SKU-92')).toBeInTheDocument()  // a generic cell for a /meta field
  })
})
