import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ListPage, PermissionProvider, RecordSheet, ViewsProvider, type ListMeta, type RefreshState, type RecordTab, type ViewsClient } from './index'
import { requestOpen } from './resource/viewRequests'
import { openTab } from './testing'

// RecordSheet (the user's ask, 2026-11-03): one detail frame for every record —
// header (title + copy, refresh, previous / next, width, close), facts,
// actions + More, tabs (counts, dots, hidden, lazy, empty), and the list it
// was opened from as its host: J / K, a linked tab, the tab kept from record
// to record, refresh of both. And every list's Refresh and auto-refresh.

type Row = { id: number; code: string }
const meta: ListMeta = { version: 1, searchable: true, default_sort: ['code'], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }
const rows: Row[] = [{ id: 1, code: 'A1' }, { id: 2, code: 'B2' }, { id: 3, code: 'C3' }]
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }

function resource(basePath = '/things') {
  return { basePath, meta: async () => meta, list: vi.fn(async () => ({ items: rows, total: rows.length })) }
}

function wrap(node: React.ReactNode, can: (k: string) => boolean = () => true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><PermissionProvider can={can}><ViewsProvider client={views}>{node}</ViewsProvider></PermissionProvider></QueryClientProvider>)
}

const tabsOf = (r: Row, rendered: string[] = []): RecordTab[] => [
  { id: 'a', label: 'Overview', render: () => { rendered.push(`a${r.id}`); return <p>overview of {r.code}</p> } },
  { id: 'b', label: 'Lines', count: 4, tone: 'warning', render: () => { rendered.push(`b${r.id}`); return <p>lines of {r.code}</p> } },
  { id: 'c', label: 'Payments', hidden: r.id === 2, render: () => <p>payments of {r.code}</p> },
  { id: 'd', label: 'Activity', empty: 'Nothing recorded yet', render: () => <p>never shown</p> },
]

beforeEach(() => { try { localStorage.clear() } catch { /* none */ } })
afterEach(() => { vi.useRealTimers() })


const stub = (p: Partial<RefreshState> = {}): RefreshState => ({
  refresh: vi.fn(async () => {}), busy: false, fetching: false, message: null, highlight: new Set(), updatedAt: 0, auto: 0, tick: 0, ...p,
})

describe('RecordSheet in a ListPage', () => {
  it('a header with the title (copyable), the facts, and tabs with counts, a dot, a hidden one, a lazy one and an empty one', async () => {
    const rendered: string[] = []
    wrap(<ListPage<Row> resource={resource()} title="Things" columns={[{ accessorKey: 'code', header: 'Code' }]}
      renderDetail={(r) => <RecordSheet title={r.code} subtitle={`Thing #${r.id}`} facts={[{ label: 'Total', value: '12.00', tone: 'warning' }]}
        tabs={tabsOf(r, rendered)} />} />)
    fireEvent.click(await screen.findByText('A1'))
    const sheet = await screen.findByRole('dialog', { name: 'A1' })
    expect(within(sheet).getByText('Thing #1')).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Copy A1' })).toBeInTheDocument()
    expect(within(sheet).getByLabelText('Key facts')).toHaveTextContent('Total12.00')
    expect(within(sheet).getByText('Total').closest('[data-tone]')).toHaveAttribute('data-tone', 'warning')
    const lines = within(sheet).getByRole('tab', { name: /^Lines/ })
    expect(lines).toHaveTextContent('4')
    expect(within(lines).getByRole('img', { name: 'needs attention' })).toBeInTheDocument()
    expect(within(sheet).getByText('overview of A1')).toBeInTheDocument()
    expect(rendered).toEqual(['a1'])  // only the chosen tab is rendered
    await openTab(sheet, 'Activity')
    expect(within(sheet).getByText('Nothing recorded yet')).toBeInTheDocument()  // its empty state, not its content
    expect(within(sheet).queryByText('never shown')).toBeNull()
  })

  it('J / K and the arrows go to the next / previous row, keeping the chosen tab; a tab the record hides falls back to the first', async () => {
    wrap(<ListPage<Row> resource={resource()} title="Things" columns={[{ accessorKey: 'code', header: 'Code' }]}
      renderDetail={(r) => <RecordSheet title={r.code} tabs={tabsOf(r)} />} />)
    fireEvent.click(await screen.findByText('A1'))
    let sheet = await screen.findByRole('dialog', { name: 'A1' })
    expect(within(sheet).getByText('1 of 3')).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Previous record (K)' })).toBeDisabled()
    await openTab(sheet, 'Lines')
    fireEvent.keyDown(sheet, { key: 'j' })
    sheet = await screen.findByRole('dialog', { name: 'B2' })
    expect(within(sheet).getByText('lines of B2')).toBeInTheDocument()  // the same tab on the next record
    await openTab(sheet, 'Overview')
    fireEvent.click(within(sheet).getByRole('button', { name: 'Next record (J)' }))
    sheet = await screen.findByRole('dialog', { name: 'C3' })
    await openTab(sheet, 'Payments')
    fireEvent.keyDown(sheet, { key: 'k' })
    sheet = await screen.findByRole('dialog', { name: 'B2' })
    expect(within(sheet).queryByRole('tab', { name: /^Payments/ })).toBeNull()  // hidden for B2
    expect(within(sheet).getByText('overview of B2')).toBeInTheDocument()     // so the first tab shows
    fireEvent.keyDown(within(sheet).getByText('overview of B2'), { key: 'ArrowUp', altKey: true })
    expect(await screen.findByRole('dialog', { name: 'A1' })).toBeInTheDocument()
  })

  it('a link opens the record on the tab it asks for (a Task Inbox link)', async () => {
    const res = resource('/linked')
    wrap(<ListPage<Row> resource={res} title="Things" columns={[{ accessorKey: 'code', header: 'Code' }]}
      renderDetail={(r) => <RecordSheet title={r.code} tabs={tabsOf(r)} />} />)
    await screen.findByText('A1')
    act(() => requestOpen('/linked', 3, 'c'))
    const sheet = await screen.findByRole('dialog', { name: 'C3' })
    expect(within(sheet).getByRole('tab', { name: /^Payments/ })).toHaveAttribute('aria-selected', 'true')
    expect(within(sheet).getByText('payments of C3')).toBeInTheDocument()
  })

  it('a sheet without a query of its own refreshes with its list; Full width is remembered; More shows only permitted actions', async () => {
    const res = resource()
    const archive = vi.fn()
    wrap(<ListPage<Row> resource={res} title="Things" columns={[{ accessorKey: 'code', header: 'Code' }]}
      renderDetail={(r) => <RecordSheet title={r.code} tabs={tabsOf(r)}
        more={[{ label: 'Archive', onSelect: archive }, { label: 'Purge', permission: 'things.purge', onSelect: vi.fn() }]} />} />,
    (k) => k !== 'things.purge')
    fireEvent.click(await screen.findByText('A1'))
    const sheet = await screen.findByRole('dialog', { name: 'A1' })
    const before = res.list.mock.calls.length
    fireEvent.click(within(sheet).getByRole('button', { name: /^Refresh/ }))
    await waitFor(() => expect(res.list.mock.calls.length).toBeGreaterThan(before))
    fireEvent.click(within(sheet).getByRole('button', { name: 'Full width' }))
    expect(localStorage.getItem('platform-ui.recordSheet.expanded')).toBe('1')
    expect(within(sheet).getByRole('button', { name: 'Narrower' })).toBeInTheDocument()
    fireEvent.keyDown(within(sheet).getByRole('button', { name: 'More actions' }), { key: 'Enter' })
    expect(await screen.findByRole('menuitem', { name: 'Archive' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Purge' })).toBeNull()  // no permission
    fireEvent.click(screen.getByRole('menuitem', { name: 'Archive' }))
    expect(archive).toHaveBeenCalledOnce()
  })

  it('while loading a skeleton; a failed load says why and tries again', async () => {
    const retry = vi.fn()
    const { rerender } = render(<RecordSheet open onOpenChange={() => {}} title="A1" loading tabs={[]} refresh={stub({ refresh: retry })} />)
    expect(screen.getByLabelText('Loading')).toHaveAttribute('aria-busy', 'true')
    rerender(<RecordSheet open onOpenChange={() => {}} title="A1" error={new Error('boom')} tabs={[]} refresh={stub({ refresh: retry })} />)
    expect(await screen.findByText(/boom/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(retry).toHaveBeenCalledOnce())
  })
})

describe('every list: Refresh and auto-refresh', () => {
  it('Refresh refetches the rows (its tooltip says when they were fetched); auto-refresh is remembered and refetches on its own', async () => {
    const res = resource('/auto')
    wrap(<ListPage<Row> resource={res} title="Things" columns={[{ accessorKey: 'code', header: 'Code' }]} />)
    await screen.findByText('A1')
    const n = res.list.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: /^Refresh · updated / }))
    await waitFor(() => expect(res.list.mock.calls.length).toBe(n + 1))
    expect(screen.getByText('A1')).toBeInTheDocument()  // the rows stay while fetching
    vi.useFakeTimers({ shouldAdvanceTime: true })
    fireEvent.keyDown(screen.getByRole('button', { name: 'Auto-refresh' }), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Every 30s' }))
    expect(localStorage.getItem('platform-ui.autoRefresh./auto')).toBe('30')
    const m = res.list.mock.calls.length
    await act(() => vi.advanceTimersByTimeAsync(31_000))
    await waitFor(() => expect(res.list.mock.calls.length).toBeGreaterThan(m))
  })

  it('a list opens with the auto-refresh the user chose for it last time', async () => {
    localStorage.setItem('platform-ui.autoRefresh./again', '300')
    wrap(<ListPage<Row> resource={resource('/again')} title="Things" columns={[{ accessorKey: 'code', header: 'Code' }]} />)
    await screen.findByText('A1')
    expect(document.querySelector('[data-auto-interval]')).toHaveTextContent('5m')  // on the refresh icon
  })
})
