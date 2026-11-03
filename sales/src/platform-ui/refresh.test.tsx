import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DataTable, ListPage, RecordSheet, ViewsProvider, type ColumnDef, type ListMeta, type ViewsClient } from './index'
import { describeDiff, describeRecordChange, diffRows } from './patterns/refreshDiff'

// A refresh says what it did (the user, 2026-11-03: "reloading is too fast, I
// can not see any changes"): a click shows the bar ≥ 600 ms, then "Up to date:
// no changes" or "2 new · 1 updated · 1 gone"; changed rows are highlighted for
// 2 s; an automatic refresh speaks only when something changed; a new query
// (page, filter, sort, view) is not a refresh.

type Row = { id: number; code: string; version?: number }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }]
const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
const bar = () => document.querySelector('main [data-busy-bar], [data-busy-bar]')
const message = () => document.querySelector('[data-refresh-message]')

function mount(list: (spec: { search?: string }) => Promise<{ items: Row[]; total: number }>, path = `/r${Math.random()}`) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
    <ListPage<Row> resource={{ basePath: path, meta: async () => meta, list }} title="Things" columns={columns} />
  </ViewsProvider></QueryClientProvider>)
  return qc
}
const refresh = () => fireEvent.click(screen.getByRole('button', { name: /^Refresh/ }))

afterEach(() => { vi.useRealTimers() })

describe('what changed, as words', () => {
  const a = [{ id: 1, code: 'A', version: 1 }, { id: 2, code: 'B', version: 1 }, { id: 3, code: 'C', version: 1 }]
  it('new, updated (version moved), gone; and new rows beyond the page from the total', () => {
    const b = [{ id: 1, code: 'A', version: 2 }, { id: 2, code: 'B', version: 1 }, { id: 4, code: 'D', version: 1 }, { id: 5, code: 'E', version: 1 }]
    expect(describeDiff(diffRows(a, b, (r) => r.id))).toBe('2 new · 1 updated · 1 gone')
    expect(describeDiff(diffRows(a, a, (r) => r.id, { before: 3, after: 6 }))).toBe('3 new (not on this page)')
    expect(describeDiff(diffRows(a, a, (r) => r.id, { before: 3, after: 3 }))).toBeNull()
    const plain = [{ id: 1, code: 'A' }]  // no version / updated_at: its fields decide
    expect(describeDiff(diffRows(plain, [{ id: 1, code: 'A2' }], (r) => r.id))).toBe('1 updated')
  })
  it("a record: the fields and the tabs' counts that moved", () => {
    expect(describeRecordChange({ status: 'Pending', version: 1 }, { status: 'Shipped', version: 2 }, { lines: 2 }, { lines: 2 })).toBe('status')
    expect(describeRecordChange({ a: 1 }, { a: 1 }, { activity: 8 }, { activity: 9 }, { activity: 'Activity' })).toBe('Activity (8→9)')
    expect(describeRecordChange({ a: 1, version: 1 }, { a: 1, version: 2 }, {}, {})).toBeNull()
  })
})

describe('a list refreshed', () => {
  it('a click: the bar at once and for ≥ 600 ms even when the fetch is quick, then "Up to date: no changes"', async () => {
    const list = vi.fn(async () => ({ items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }))
    mount(list)
    await screen.findByText('A1')
    refresh()
    await waitFor(() => expect(bar()).not.toBeNull())
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    await new Promise((r) => setTimeout(r, 300))
    expect(bar()).not.toBeNull()  // the fetch is long done; the bar stays to 600 ms
    await waitFor(() => expect(bar()).toBeNull(), { timeout: 1500 })
    expect(message()).toHaveTextContent('Up to date: no changes')
    expect(message()).toHaveAttribute('aria-live', 'polite')
  })

  it('new, updated and gone rows: said, and the new and changed ones highlighted, cleared after 2 s', async () => {
    let n = 0
    const list = vi.fn(async () => (n++ === 0
      ? { items: [{ id: 1, code: 'A1', version: 1 }, { id: 2, code: 'B2', version: 1 }, { id: 3, code: 'C3', version: 1 }], total: 3 }
      : { items: [{ id: 1, code: 'A1', version: 2 }, { id: 2, code: 'B2', version: 1 }, { id: 4, code: 'D4', version: 1 }, { id: 5, code: 'E5', version: 1 }], total: 4 }))
    mount(list)
    await screen.findByText('C3')
    refresh()
    expect(await screen.findByText('D4')).toBeInTheDocument()
    expect(screen.queryByText('C3')).toBeNull()  // gone: simply not there
    await waitFor(() => expect(message()).toHaveTextContent('2 new · 1 updated · 1 gone'), { timeout: 1500 })
    const lit = [...document.querySelectorAll('tr[data-highlight]')].map((tr) => tr.textContent)
    expect(lit.sort()).toEqual(['A1', 'D4', 'E5'])
    await waitFor(() => expect(document.querySelector('tr[data-highlight]')).toBeNull(), { timeout: 3000 })
  })

  it('a failed refresh says so', async () => {
    let n = 0
    const list = vi.fn(async () => { if (n++ > 0) throw new Error('down'); return { items: [{ id: 1, code: 'A1' }], total: 1 } })
    mount(list)
    await screen.findByText('A1')
    refresh()
    await waitFor(() => expect(message()).toHaveTextContent('Refresh failed: try again'), { timeout: 1500 })
  })

  it('a new search is a new query: no highlight, no message', async () => {
    const list = vi.fn(async (spec: { search?: string }) => (spec.search
      ? { items: [{ id: 9, code: 'Z9', version: 1 }], total: 1 } : { items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }))
    mount(list)
    await screen.findByText('A1')
    const box = screen.getByRole('textbox', { name: 'Search' })
    fireEvent.change(box, { target: { value: 'Z' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(await screen.findByText('Z9')).toBeInTheDocument()
    expect(document.querySelector('tr[data-highlight]')).toBeNull()
    expect(message()).toHaveTextContent('')
  })

  it('an automatic refresh is silent when nothing changed, and says it when something did', async () => {
    let rows = [{ id: 1, code: 'A1', version: 1 }]
    const list = vi.fn(async () => ({ items: rows, total: rows.length }))
    const qc = mount(list, '/auto-silent')
    await screen.findByText('A1')
    await act(() => qc.invalidateQueries())  // what auto-refresh and an action's invalidation do
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(message()).toHaveTextContent('')
    rows = [...rows, { id: 2, code: 'B2', version: 1 }]
    await act(() => qc.invalidateQueries())
    expect(await screen.findByText('B2')).toBeInTheDocument()
    await waitFor(() => expect(message()).toHaveTextContent('1 new'))
    expect(document.querySelector('tr[data-highlight]')?.textContent).toBe('B2')
  })
})

describe('a background fetch', () => {
  it('quicker than 200 ms: no bar at all', async () => {
    vi.useFakeTimers()
    const rows = [{ id: 1, code: 'A1' }]
    const { rerender } = render(<DataTable<Row> ariaLabel="T" columns={columns} rows={rows} rowKey={(r) => r.id} fetching />)
    act(() => { vi.advanceTimersByTime(120) })
    rerender(<DataTable<Row> ariaLabel="T" columns={columns} rows={rows} rowKey={(r) => r.id} fetching={false} />)
    act(() => { vi.advanceTimersByTime(500) })
    expect(bar()).toBeNull()
  })
})

describe('a record refreshed', () => {
  it('"Up to date", or "Updated: <what>" with the fields and tab counts that moved', async () => {
    let rec = { status: 'Pending', version: 1 }
    let count = 2
    function Sheet() {
      return <RecordSheet open onOpenChange={() => {}} title="A1" snapshot={rec} onRefresh={async () => { rec = { status: 'Shipped', version: 2 }; count = 3 }}
        tabs={[{ id: 'lines', label: 'Lines', count, render: () => <p>lines</p> }]} />
    }
    const { rerender } = render(<Sheet />)
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    rerender(<Sheet />)  // the new record arrives
    await waitFor(() => expect(within(screen.getByRole('dialog')).getByText(/^Updated: /)).toHaveTextContent('Updated: Lines (2→3), status'),
      { timeout: 1500 })
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(within(screen.getByRole('dialog')).getByText('Up to date')).toBeInTheDocument(), { timeout: 1500 })
  })
})
