import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ListPage, RecordSheet, ViewsProvider, type ColumnDef, type QuerySpec } from './index'
import type { FieldMeta, ListMeta } from './resource/listMeta'
import type { ViewsClient } from './resource/viewsApi'

// The search panel (#32 b, the user's design and mock-ups of 2026-11-26): on the right, the table in sight; In use on top with
// ✕, a cleared row struck through until ✕; the other fields in their groups (the shown columns first, "in the table"); Find a
// field; Clear all, Back to the view, Search (n) counted after a pause; a record sheet closes it and keeps the draft; Tab stays
// inside, Esc closes, focus back to the icon; what Search found is said in the status row.

type Row = { id: number; code: string; partner: string }
const OPS: FieldMeta['operators'] = ['contains', 'not_contains', 'starts_with', 'ends_with', 'eq', 'ne', 'in', 'not_in', 'is_null', 'not_null']
const f = (name: string, label: string, group: string, type: FieldMeta['type'] = 'text', operators = OPS): FieldMeta =>
  ({ name, label, type, enum_values: [], filterable: true, sortable: true, groupable: false, operators, group })
const DATE_OPS: FieldMeta['operators'] = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in', 'is_null', 'not_null']
const meta: ListMeta = {
  version: 1, searchable: true, default_sort: ['code'], groups: ['Order', 'Partner', 'Dates', 'Other'],
  fields: [f('tags', 'Tags', 'Order'), f('source', 'Source no.', 'Order'), f('code', 'Order ref', 'Order'), f('partner', 'Partner', 'Partner'),
    f('received', 'Received', 'Dates', 'datetime', DATE_OPS), f('ordered', 'Order date', 'Dates', 'date', DATE_OPS), f('note', 'Note', 'Other')],
  views: [{ key: 'open', name: 'Open', state: { version: 1, filters: [{ field: 'partner', op: 'contains', value: 'Jean' }] } }],
}
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Order ref' }, { accessorKey: 'partner', header: 'Partner' }]

function mount(count: (spec: QuerySpec) => Promise<{ total: number }> = vi.fn(async () => ({ total: 42 }))) {
  const list = vi.fn(async (spec: QuerySpec) => ({ items: [{ id: 1, code: 'JC-1', partner: 'Jean Coutu' }, { id: 2, code: 'NW-2', partner: 'Northwind' }]
    .slice(0, (spec.filters ?? []).length ? 1 : 2), total: (spec.filters ?? []).length ? 1 : 2 }))
  const resource = { basePath: '/orders', meta: async () => meta, list, count }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
    <ListPage<Row> resource={resource} title="Orders" columns={columns}
      renderDetail={(r) => <RecordSheet title={`Order ${r.code}`} tabs={[{ id: 'd', label: 'Detail', render: () => <p>detail</p> }]} />} />
  </ViewsProvider></QueryClientProvider>)
  return { list, count, last: () => list.mock.calls.at(-1)![0] as QuerySpec }
}
const openPanel = async () => {
  fireEvent.click(await screen.findByRole('button', { name: /^Search on any field/ }))
  return screen.findByRole('dialog', { name: 'Search on any field' })
}

afterEach(() => vi.useRealTimers())

describe('the search panel', () => {
  it('beside the table, not over it: the rows stay reachable; In use from the view, its ✕ and a cleared row struck until ✕', async () => {
    const t = mount()
    await screen.findByText('JC-1')  // the view Open: Partner contains Jean
    const panel = await openPanel()
    expect(panel).toHaveAttribute('aria-modal', 'false')
    expect(screen.getByText('JC-1')).toBeVisible()
    const inUse = within(panel).getByRole('region', { name: 'In use' })
    expect(inUse).toHaveTextContent('In use (1)')
    fireEvent.click(within(panel).getByRole('button', { name: 'Clear all' }))
    expect(within(inUse).getByText('Partner')).toHaveClass('line-through')
    expect(inUse).toHaveTextContent('In use (0)')
    expect(within(panel).getByText('Cleared: Search takes it off the list. ✕ takes the row away.')).toBeInTheDocument()
    fireEvent.click(within(inUse).getByRole('button', { name: 'Remove Partner' }))  // ✕: the row goes
    expect(within(panel).queryByRole('region', { name: 'In use' })).toBeNull()
    fireEvent.click(within(panel).getByRole('button', { name: /^Search/ }))
    await waitFor(() => expect(t.last().filters ?? []).toEqual([]))
  })

  it('groups in the server\'s order, the shown columns first with "in the table"; the same counts; Find a field', async () => {
    mount()
    await screen.findByText('JC-1')
    const panel = await openPanel()
    const order = within(panel).getByRole('region', { name: 'Order' })
    expect(within(order).getByRole('button', { name: /^Order/ })).toHaveTextContent('Order (3)')
    const names = within(order).getAllByRole('button', { name: /^Add / }).map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(['Add Order ref', 'Add Tags', 'Add Source no.'])  // the shown column first, then as declared
    expect(within(order).getByRole('button', { name: 'Add Order ref' })).toHaveTextContent('in the table')
    expect(within(order).getByRole('button', { name: 'Add Tags' })).not.toHaveTextContent('in the table')
    const dates = within(panel).getByRole('region', { name: 'Dates' })
    expect(within(dates).getByRole('button', { name: /^Dates/ })).toHaveAttribute('aria-expanded', 'false')  // folded
    expect(within(dates).getByRole('button', { name: /^Dates/ })).toHaveTextContent('Dates (2)')
    expect(within(panel).queryByRole('region', { name: 'Partner' })).toBeNull()  // its one field is in use
    fireEvent.change(within(panel).getByLabelText('Find a field'), { target: { value: 'rec' } })
    expect(within(panel).getAllByRole('button', { name: /^Add / }).map((b) => b.getAttribute('aria-label'))).toEqual(['Add Received'])
    expect(within(panel).getByText('Rec').tagName).toBe('MARK')
    fireEvent.keyDown(within(panel).getByLabelText('Find a field'), { key: 'Enter' })  // the one match: added, not searched
    expect(screen.getByRole('dialog', { name: 'Search on any field' })).toBeInTheDocument()
    expect(within(panel).getByRole('region', { name: 'In use' })).toHaveTextContent('Received')
    expect(within(panel).getByLabelText('Received: how to compare')).toHaveTextContent('is within')
  })

  it('Search (n): counted once after a pause, an older answer dropped; the list is not read meanwhile', async () => {
    let answer: (n: { total: number }) => void = () => {}
    const count = vi.fn((spec: QuerySpec) => (spec.filters?.some((x) => x.value === 'Je')
      ? new Promise<{ total: number }>((r) => { answer = r })            // an older draft: answers late
      : Promise.resolve({ total: 7 })))
    const t = mount(count)
    await screen.findByText('JC-1')
    const reads = t.list.mock.calls.length
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const panel = await openPanel()
    const box = within(panel).getByRole('region', { name: 'In use' })
    const value = within(box).getByLabelText('Partner: value')
    fireEvent.click(within(box).getByRole('button', { name: 'Remove Jean' }))
    fireEvent.change(value, { target: { value: 'J' } })
    fireEvent.change(value, { target: { value: 'Je' } })
    await act(async () => { vi.advanceTimersByTime(450) })
    fireEvent.change(value, { target: { value: 'Jea' } })
    const sofar = count.mock.calls.length
    await act(async () => { vi.advanceTimersByTime(300) })
    expect(count.mock.calls.length).toBe(sofar)                         // not before the pause
    await act(async () => { vi.advanceTimersByTime(150) })
    await waitFor(() => expect(within(panel).getByRole('button', { name: 'Search (7)' })).toBeInTheDocument())
    await act(async () => { answer({ total: 99 }) })                      // the late answer for "Je": dropped
    expect(within(panel).getByRole('button', { name: 'Search (7)' })).toBeInTheDocument()
    const asked = count.mock.calls.map(([s]) => (s.filters ?? []).map((x) => x.value).join())
    expect(asked).not.toContain('J')                                    // the pause: a draft typed through is never counted
    expect(t.list.mock.calls.length).toBe(reads)                        // counting reads no rows
  })

  it('Back to the view returns to its search; a record sheet closes the panel and keeps the draft', async () => {
    const t = mount()
    await screen.findByText('JC-1')
    let panel = await openPanel()
    expect(within(panel).getByRole('button', { name: 'Back to the view' })).toBeDisabled()  // nothing changed yet
    fireEvent.click(within(panel).getByRole('button', { name: 'Add Tags' }))
    fireEvent.change(within(panel).getByLabelText('Tags: value'), { target: { value: 'rush' } })
    fireEvent.click(screen.getByText('JC-1'))                            // a row: its sheet opens, the panel closes
    expect(await screen.findByRole('dialog', { name: 'Order JC-1' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Search on any field' })).toBeNull()
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Order JC-1' }), { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Order JC-1' })).toBeNull())
    panel = await openPanel()
    expect(within(panel).getByLabelText('Tags: value')).toHaveValue('rush')  // the draft kept
    fireEvent.click(within(panel).getByRole('button', { name: 'Back to the view' }))
    expect(within(panel).queryByLabelText('Tags: value')).toBeNull()
    expect(within(panel).getByRole('region', { name: 'In use' })).toHaveTextContent('Partner')
    fireEvent.keyDown(panel, { key: 'Escape' })                          // Esc: closed, the draft dropped, focus to the icon
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Search on any field' })).toBeNull())
    await waitFor(() => expect(screen.getByRole('button', { name: /^Search on any field/ })).toHaveFocus())
    expect(t.last().filters).toEqual([{ field: 'partner', op: 'contains', value: 'Jean' }])  // nothing applied
    panel = await openPanel()
    expect(within(panel).queryByLabelText('Tags: value')).toBeNull()      // Esc dropped the draft: it starts from the search
  })

  it('Tab stays inside while it is open; Search says what it found and closes', async () => {
    const t = mount()
    await screen.findByText('JC-1')
    const panel = await openPanel()
    const focusable = [...panel.querySelectorAll<HTMLElement>('input, button')].filter((el) => !el.hasAttribute('disabled'))
    const last = focusable[focusable.length - 1]
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(focusable[0]).toHaveFocus()
    fireEvent.keyDown(focusable[0], { key: 'Tab', shiftKey: true })
    expect(last).toHaveFocus()
    fireEvent.click(within(panel).getByRole('button', { name: 'Add Tags' }))
    fireEvent.change(within(panel).getByLabelText('Tags: value'), { target: { value: 'rush' } })
    fireEvent.click(within(panel).getByRole('button', { name: /^Search/ }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Search on any field' })).toBeNull())
    expect(t.last().filters).toEqual([{ field: 'partner', op: 'contains', value: 'Jean' }, { field: 'tags', op: 'contains', value: 'rush' }])
    expect(await screen.findByText('1 row matches the search.')).toBeInTheDocument()  // said in the status row, once its rows came
    await waitFor(() => expect(screen.getByRole('button', { name: /^Search on any field/ })).toHaveFocus())
  })
})
