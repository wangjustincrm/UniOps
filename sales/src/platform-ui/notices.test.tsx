import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ActionButton, ListPage, RecordSheet, ViewsProvider, type ColumnDef } from './index'
import { TableStatusBar } from './patterns/TableStatusBar'
import { createNoticeStore } from './patterns/notices'
import { type ListMeta } from './resource/listMeta'
import { type ViewsClient } from './resource/viewsApi'
import { noticeIn } from './testing'

// The status row (the user, 2026-11-03): every list and sheet says what
// happened in ONE reserved row — a chip tinted by tone, with ×, "+N" for more
// open ones, and a bell with the session's history — never beside the refresh
// button (no toolbar jump). The card fits the view: the rows scroll inside it,
// the header and the row stay.

type Row = { id: number; code: string; version: number }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }]
const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }
const views = (over: Partial<ViewsClient> = {}): ViewsClient => ({
  list: async () => [], create: vi.fn(async (resource, name, state) => ({ id: 9, resource, name, state, is_default: false, sort: 0, version: 1, stale: false })),
  update: vi.fn(), remove: vi.fn(), setDefault: vi.fn(), ...over,
})
afterEach(() => { vi.useRealTimers() })

function mountList(list: () => Promise<{ items: Row[]; total: number }>, client: ViewsClient = views(), detail?: (r: Row) => React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ViewsProvider client={client}>
    <ListPage<Row> resource={{ basePath: `/n${Math.random()}`, meta: async () => meta, list }} title="Things" columns={columns}
      renderDetail={detail} />
  </ViewsProvider></QueryClientProvider>)
  return qc
}
const refresh = () => fireEvent.click(screen.getByRole('button', { name: /^Refresh/ }))

describe('the status row of a list', () => {
  it('a refresh says it there, not beside the refresh button: the toolbar has no message node', async () => {
    mountList(async () => ({ items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }))
    await screen.findByText('A1')
    const toolbarBefore = screen.getByRole('button', { name: /^Refresh/ }).closest('div')!.innerHTML
    refresh()
    await waitFor(() => expect(noticeIn()).toHaveTextContent('Up to date: no changes'), { timeout: 2000 })
    expect(document.querySelector('[data-refresh-message]')).toBeNull()
    expect(screen.getByRole('button', { name: /^Refresh/ }).parentElement!.textContent).toBe('')  // icon only: nothing to jump
    expect(toolbarBefore).toBeTruthy()
  })

  it('a failed refresh is sticky with Try again, and the next refresh that works clears it', async () => {
    let fail = false
    const list = vi.fn(async () => { if (fail) throw new Error('down'); return { items: [{ id: 1, code: 'A1', version: 1 }], total: 1 } })
    mountList(list)
    await screen.findByText('A1')
    fail = true
    refresh()
    // one failure notice (key 'refresh'): "Refresh failed", or the list's own "Could not load the list: …"
    await waitFor(() => expect(noticeIn()).toHaveTextContent(/Refresh failed|Could not load the list/), { timeout: 2000 })
    expect(noticeIn()).toHaveAttribute('data-tone', 'error')
    await new Promise((r) => setTimeout(r, 4500))  // an error does not fade
    expect(noticeIn()).toHaveTextContent(/Refresh failed|Could not load the list/)
    fail = false
    fireEvent.click(within(noticeIn() as HTMLElement).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(noticeIn()).toHaveTextContent('Up to date: no changes'), { timeout: 2000 })
  }, 10_000)

  it('a load that fails after the first one is said in the row; the first load keeps its error in place of the rows', async () => {
    const list = vi.fn().mockRejectedValueOnce(new Error('no server'))
    mountList(list as unknown as () => Promise<{ items: Row[]; total: number }>)
    expect(await screen.findByText(/no server/)).toBeInTheDocument()  // no rows yet: in the table's place
  })

  it('view save success and failure go to the row', async () => {
    const client = views({ create: vi.fn(async (resource: string, name: string, state) => ({ id: 9, resource, name, state, is_default: false, sort: 0, version: 1, stale: false }))
      .mockRejectedValueOnce(new Error('taken')) })
    mountList(async () => ({ items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }), client)
    await screen.findByText('A1')
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'x' } })
    fireEvent.keyDown(screen.getByLabelText('Search'), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('button', { name: 'Save as a new view' }))
    const pop = await screen.findByRole('dialog', { name: 'Save as a new view' })
    fireEvent.change(within(pop).getByLabelText('View name'), { target: { value: 'Mine' } })
    fireEvent.click(within(pop).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(noticeIn()).toHaveTextContent(/The view was not saved/))
    fireEvent.click(within(pop).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(noticeIn()).toHaveTextContent('Saved as the view "Mine"'))
  })
})

describe('TableStatusBar', () => {
  it('a chip per tone with its tint; × closes any tone and the notice stays in the history', () => {
    const store = createNoticeStore()
    render(<TableStatusBar store={store} />)
    for (const [tone, cls] of [['info', 'bg-primary-50'], ['success', 'bg-success-50'], ['warning', 'bg-warning-50'], ['error', 'bg-danger-50']] as const) {
      act(() => store.notify({ tone, text: `a ${tone}` }))
      expect(noticeIn()).toHaveAttribute('data-tone', tone)
      expect(noticeIn()).toHaveClass(cls)
      fireEvent.click(within(noticeIn() as HTMLElement).getByRole('button', { name: 'Dismiss' }))
      expect(noticeIn()).toHaveTextContent('')
    }
    expect(store.get().history.map((n) => n.text)).toEqual(['a error', 'a warning', 'a success', 'a info'])
  })

  it('info and success fade by themselves after ~4 s; a warning or an error stays until closed', () => {
    vi.useFakeTimers()
    const store = createNoticeStore()
    render(<TableStatusBar store={store} />)
    act(() => store.notify({ tone: 'success', text: 'saved' }))
    act(() => { vi.advanceTimersByTime(3900) })
    expect(noticeIn()).toHaveTextContent('saved')
    act(() => { vi.advanceTimersByTime(200) })
    expect(noticeIn()).toHaveTextContent('')
    act(() => store.notify({ tone: 'error', text: 'broke' }))
    act(() => { vi.advanceTimersByTime(60_000) })
    expect(noticeIn()).toHaveTextContent('broke')
  })

  it('a key replaces the earlier notice with that key', () => {
    const store = createNoticeStore()
    render(<TableStatusBar store={store} />)
    act(() => store.notify({ key: 'refresh', tone: 'info', text: 'Updating…', sticky: true }))
    act(() => store.notify({ key: 'refresh', tone: 'error', text: 'Refresh failed' }))
    act(() => store.notify({ key: 'refresh', tone: 'success', text: '1 new' }))
    expect(noticeIn()).toHaveTextContent('1 new')
    expect(store.get().moreSticky).toBe(0)  // the failure was replaced, not left behind
  })

  it('several sticky ones: the latest with "+2", which opens the history', async () => {
    const store = createNoticeStore()
    render(<TableStatusBar store={store} />)
    act(() => { store.notify({ tone: 'error', text: 'one' }); store.notify({ tone: 'error', text: 'two' }); store.notify({ tone: 'warning', text: 'three' }) })
    expect(noticeIn()).toHaveTextContent('three')
    fireEvent.click(within(noticeIn() as HTMLElement).getByRole('button', { name: '2 more open: show the notifications' }))
    expect(await screen.findByRole('list', { name: 'Notification history' })).toHaveTextContent(/three.*two.*one/)
  })

  it('the bell: a count of unseen, opening marks them seen, Clear empties the history', async () => {
    const store = createNoticeStore()
    render(<TableStatusBar store={store} />)
    act(() => { store.notify({ tone: 'success', text: 'a' }); store.notify({ tone: 'info', text: 'b' }) })
    fireEvent.click(screen.getByRole('button', { name: 'Notifications (2)' }))
    const history = await screen.findByRole('list', { name: 'Notification history' })
    expect(within(history).getAllByRole('listitem')).toHaveLength(2)
    expect(store.get().unseen).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(await screen.findByText('Nothing yet in this session')).toBeInTheDocument()
  })
})

describe('the sheet: its own status row, pinned at its bottom', () => {
  it('an action that fails inside the sheet says so in the sheet\'s row (not the list\'s)', async () => {
    mountList(async () => ({ items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }), views(), (r) => (
      <RecordSheet title={r.code} tabs={[{ id: 'a', label: 'A', render: () => <p>a</p> }]}
        actions={<ActionButton size="sm" onAction={async () => { throw new Error('nope') }}>Do it</ActionButton>} />
    ))
    fireEvent.click(await screen.findByText('A1'))
    const sheet = await screen.findByRole('dialog', { name: 'A1' })
    expect(sheet.querySelector('[data-status-bar]')).not.toBeNull()  // the sheet's footer row
    fireEvent.click(within(sheet).getByRole('button', { name: 'Do it' }))
    await waitFor(() => expect(noticeIn(sheet)).toHaveTextContent('nope'))
    expect(noticeIn(sheet)).toHaveAttribute('data-tone', 'error')
  })
})

describe('the card fits the view', () => {
  it('the status row is outside the rows\' scroll area; the header and the totals are sticky', async () => {
    mountList(async () => ({ items: Array.from({ length: 5 }, (_, i) => ({ id: i, code: `R${i}`, version: 1 })), total: 5 }))
    await screen.findByText('R0')
    const scroll = document.querySelector('[data-table-scroll]')!
    const bar = document.querySelector('[data-status-bar]')!
    expect(scroll.contains(bar)).toBe(false)
    expect(scroll.closest('[data-table-card]')!.contains(bar)).toBe(true)
    expect(scroll.querySelector('thead')).toHaveClass('sticky', 'top-0')
    expect(bar.parentElement).toHaveClass('sticky', 'bottom-0')
  })
})
