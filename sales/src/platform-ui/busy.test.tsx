import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ListPage, LocalTable, RecordSheet, ViewsProvider, type ColumnDef } from './index'
import { DataTable } from './primitives/DataTable'
import { type ListMeta } from './resource/listMeta'
import { type RefreshState } from './patterns/useRefresh'
import { createNoticeStore } from './patterns/notices'
import { type ViewsClient } from './resource/viewsApi'

// Loading feedback (the user, 2026-11-03: "tables show nothing while loading"):
// a refetch over rows shown puts a thin bar on the card's top edge (after
// 200 ms), aria-busy and a polite "Loading…"; rows of the previous query are
// dimmed with "Updating…"; the first load keeps its skeleton rows.

type Row = { id: number; code: string }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }]
const rows: Row[] = [{ id: 1, code: 'A1' }, { id: 2, code: 'B2' }]
const bar = () => document.querySelector('[data-busy-bar]')

afterEach(() => { vi.useRealTimers() })


const stub = (p: Partial<RefreshState> = {}): RefreshState => ({
  refresh: vi.fn(async () => {}), busy: false, fetching: false, notices: createNoticeStore(), highlight: new Set(), updatedAt: 0, auto: 0, tick: 0, ...p,
})

describe('DataTable while fetching', () => {
  it('a refetch over rows shown: the bar and aria-busy after 200 ms — not for a quicker fetch', async () => {
    vi.useFakeTimers()
    const { rerender } = render(<DataTable<Row> ariaLabel="Things" columns={columns} rows={rows} rowKey={(r) => r.id} fetching />)
    expect(bar()).toBeNull()  // not at once
    act(() => { vi.advanceTimersByTime(150) })
    expect(bar()).toBeNull()
    rerender(<DataTable<Row> ariaLabel="Things" columns={columns} rows={rows} rowKey={(r) => r.id} fetching={false} />)
    act(() => { vi.advanceTimersByTime(200) })
    expect(bar()).toBeNull()  // done in 150 ms: never flashed
    rerender(<DataTable<Row> ariaLabel="Things" columns={columns} rows={rows} rowKey={(r) => r.id} fetching />)
    act(() => { vi.advanceTimersByTime(210) })
    expect(bar()).not.toBeNull()
    expect(screen.getByRole('table', { name: 'Things' })).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('Loading…')
    expect(screen.getByText('A1')).toBeInTheDocument()  // the rows stay
    expect(screen.queryByText('Updating…')).toBeNull()  // the same query: not dimmed
  })

  it("the previous query's rows (another page, sort, filter): dimmed, with Updating… and the footer dimmed too", async () => {
    vi.useFakeTimers()
    render(<DataTable<Row> ariaLabel="Things" columns={columns} rows={rows} rowKey={(r) => r.id} fetching stale footer={['Total 2']}
      pagination={{ page: 1, pageSize: 50, total: 2, onPageChange: () => {}, onPageSizeChange: () => {} }} />)
    act(() => { vi.advanceTimersByTime(210) })
    const table = screen.getByRole('table', { name: 'Things' })
    expect(table.querySelector('tbody')).toHaveAttribute('data-stale', 'true')
    expect(table.querySelector('tbody')).toHaveClass('opacity-60')
    expect(table.querySelector('tfoot')).toHaveClass('opacity-60')
  })

  it('the first load: skeleton rows, no bar', async () => {
    vi.useFakeTimers()
    render(<DataTable<Row> ariaLabel="Things" columns={columns} rows={[]} rowKey={(r) => r.id} loading fetching />)
    act(() => { vi.advanceTimersByTime(500) })
    expect(bar()).toBeNull()
    expect(screen.getByRole('table', { name: 'Things' })).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('table', { name: 'Things' }).querySelectorAll('tbody tr').length).toBe(5)  // the skeleton
  })

  it('LocalTable takes the same fetching', async () => {
    vi.useFakeTimers()
    render(<LocalTable<Row> ariaLabel="Lines" columns={columns} rows={rows} rowKey={(r) => r.id} fetching />)
    act(() => { vi.advanceTimersByTime(210) })
    expect(bar()).not.toBeNull()
  })
})

describe('a list changing page: the old rows dimmed until the new ones arrive', () => {
  it('ListPage passes fetching and placeholder data through', async () => {
    const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }
    const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
    let release: (() => void) | null = null
    const list = vi.fn(async (spec: { page: number }) => {
      if (spec.page === 1) return { items: [{ id: 1, code: 'A1' }], total: 60 }
      await new Promise<void>((r) => { release = r })  // page 2 is slow
      return { items: [{ id: 51, code: 'P2' }], total: 60 }
    })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
      <ListPage<Row> resource={{ basePath: '/paged', meta: async () => meta, list }} title="Things" columns={columns} />
    </ViewsProvider></QueryClientProvider>)
    await screen.findByText('A1')
    fireEvent.click(screen.getByRole('button', { name: /next/i }))
    await waitFor(() => expect(screen.getByText('Updating…')).toBeInTheDocument(), { timeout: 2000 })
    expect(screen.getByText('A1')).toBeInTheDocument()  // the old page, dimmed, still there
    await waitFor(() => expect(screen.getByRole('table').querySelector('tbody')).toHaveAttribute('data-stale', 'true'))
    act(() => release!())
    expect(await screen.findByText('P2')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Updating…')).toBeNull())
  })
})

describe('RecordSheet while the record refetches', () => {
  it('the bar under its header; none while it first loads (skeleton)', async () => {
    vi.useFakeTimers()
    const { rerender } = render(<RecordSheet open onOpenChange={() => {}} title="A1" loading refresh={stub({ fetching: true })} tabs={[]} />)
    act(() => { vi.advanceTimersByTime(300) })
    expect(bar()).toBeNull()
    rerender(<RecordSheet open onOpenChange={() => {}} title="A1" refresh={stub({ fetching: true })} tabs={[{ id: 'a', label: 'Overview', render: () => <p>x</p> }]} />)
    act(() => { vi.advanceTimersByTime(210) })
    expect(within(screen.getByRole('dialog')).getByText('Loading…')).toBeInTheDocument()
    expect(bar()).not.toBeNull()
  })
})
