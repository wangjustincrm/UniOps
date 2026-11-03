import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ListPage, RecordSheet, RefreshControl, ViewsProvider, compareRecord, compareRows, useRefresh,
  type ColumnDef, type ListMeta, type ViewsClient,
} from './index'

// ONE refresh (the user, 2026-11-03): a click, an auto-refresh tick and a record
// sheet's refresh go through useRefresh's one function — the same spin, the
// same bar, the same words ("Up to date: no changes" / "1 new · 1 updated"),
// the same highlight; RefreshControl is the one control (the icon refreshes;
// the chevron chooses Off / 30s / 1m / 5m; the interval sits on the icon).

type Row = { id: number; code: string; version: number }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }]
const meta: ListMeta = {
  version: 1, searchable: true, default_sort: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }],
  fields: [{ name: 'code', label: 'Code', type: 'text', enum_values: [], filterable: true, sortable: true, groupable: false, operators: ['contains'] }],
}
const views: ViewsClient = { list: async () => [], create: vi.fn(async (resource, name, state) => ({ id: 9, resource, name, state, is_default: false, sort: 0, version: 1, stale: false })),
  update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
const message = (within_: HTMLElement | Document = document) => within_.querySelector('[data-refresh-message]')!
const refreshButton = () => screen.getByRole('button', { name: /^Refresh/ })

let visibility: DocumentVisibilityState = 'visible'
beforeEach(() => {
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
  try { localStorage.clear() } catch { /* none */ }
})
afterEach(() => { vi.useRealTimers() })
const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms))

/** One query, one useRefresh, its RefreshControl; the refetch it is given is a spy (the single path). */
function Harness({ list, spy, initialAuto = 0 }: { list: () => Promise<{ items: Row[]; total: number }>; spy: (fn: () => Promise<{ isError: boolean }>) => Promise<{ isError: boolean }>; initialAuto?: number }) {
  const [auto, setAuto] = useState(initialAuto)
  const q = useQuery({ queryKey: ['harness'], queryFn: list })
  const r = useRefresh({ ...q, refetch: () => spy(() => q.refetch()) }, { queryKey: ['harness'], compare: compareRows<Row>((x) => x.id), auto, setAuto })
  return (
    <>
      <RefreshControl state={r} />
      <ul>{(q.data?.items ?? []).map((x) => <li key={x.id} data-lit={r.highlight.has(x.id) || undefined}>{x.code}</li>)}</ul>
    </>
  )
}
function mountHarness(list: () => Promise<{ items: Row[]; total: number }>, initialAuto = 0) {
  const spy = vi.fn((fn: () => Promise<{ isError: boolean }>) => fn())
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><Harness list={list} spy={spy} initialAuto={initialAuto} /></QueryClientProvider>)
  return { spy, qc }
}

describe('RefreshControl and useRefresh', () => {
  it('a click: the icon spins (≥ 600 ms), the query is fetched again through the one function, then "Up to date: no changes"', async () => {
    const list = vi.fn(async () => ({ items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }))
    const { spy } = mountHarness(list)
    await screen.findByText('A1')
    fireEvent.click(refreshButton())
    await waitFor(() => expect(refreshButton()).toHaveAttribute('aria-busy', 'true'))
    expect(refreshButton().querySelector('svg')).toHaveClass('animate-spin')
    await waitFor(() => expect(message()).toHaveTextContent('Up to date: no changes'), { timeout: 1500 })
    expect(spy).toHaveBeenCalledOnce()
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('the menu sets 30s: "30" on the icon and the tint; Off removes them; the tooltip says it', async () => {
    mountHarness(vi.fn(async () => ({ items: [{ id: 1, code: 'A1', version: 1 }], total: 1 })))
    await screen.findByText('A1')
    expect(document.querySelector('[data-auto-interval]')).toBeNull()
    fireEvent.keyDown(screen.getByRole('button', { name: 'Auto-refresh' }), { key: 'Enter' })
    const items = await screen.findAllByRole('menuitem')
    expect(items.map((i) => i.textContent)).toEqual(['Off', 'Every 30s', 'Every 1m', 'Every 5m'])
    fireEvent.click(screen.getByRole('menuitem', { name: 'Every 30s' }))
    await waitFor(() => expect(document.querySelector('[data-auto-interval]')).toHaveTextContent('30'))
    expect(refreshButton()).toHaveAccessibleName(/^Refresh · auto every 30s · updated /)
    fireEvent.keyDown(screen.getByRole('button', { name: 'Auto-refresh' }), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Off' }))
    await waitFor(() => expect(document.querySelector('[data-auto-interval]')).toBeNull())
  })

  it('an auto tick goes through the same function, with the same spin and the same words; a change is highlighted', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let n = 0
    const list = vi.fn(async () => (n++ < 2
      ? { items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }
      : { items: [{ id: 1, code: 'A1', version: 2 }, { id: 2, code: 'B2', version: 1 }], total: 2 }))
    const { spy } = mountHarness(list, 30)
    await screen.findByText('A1')
    await advance(30_000)
    expect(refreshButton()).toHaveAttribute('aria-busy', 'true')  // the same spin as a click
    await advance(700)
    expect(spy).toHaveBeenCalledOnce()  // the one function
    expect(message()).toHaveTextContent('Up to date: no changes')  // the same words, no "Auto-refreshed"
    await advance(30_000)
    await advance(700)
    expect(spy).toHaveBeenCalledTimes(2)
    expect(message()).toHaveTextContent('1 new · 1 updated')
    expect([...document.querySelectorAll('li[data-lit]')].map((li) => li.textContent).sort()).toEqual(['A1', 'B2'])
  })

  it('a hidden tab does not tick; shown again, it ticks at once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const list = vi.fn(async () => ({ items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }))
    const { spy } = mountHarness(list, 30)
    await screen.findByText('A1')
    act(() => { visibility = 'hidden'; document.dispatchEvent(new Event('visibilitychange')) })
    await advance(90_000)
    expect(spy).not.toHaveBeenCalled()
    act(() => { visibility = 'visible'; document.dispatchEvent(new Event('visibilitychange')) })
    await advance(700)
    expect(spy).toHaveBeenCalledOnce()
    expect(message()).toHaveTextContent('Up to date: no changes')
  })

  it('an incidental fetch (an action, the window focus) speaks only when something changed', async () => {
    let rows = [{ id: 1, code: 'A1', version: 1 }]
    const { qc, spy } = mountHarness(vi.fn(async () => ({ items: rows, total: rows.length })))
    await screen.findByText('A1')
    await act(() => qc.invalidateQueries())
    await new Promise((r) => setTimeout(r, 50))
    expect(message()).toHaveTextContent('')
    rows = [...rows, { id: 2, code: 'B2', version: 1 }]
    await act(() => qc.invalidateQueries())
    expect(await screen.findByText('B2')).toBeInTheDocument()
    await waitFor(() => expect(message()).toHaveTextContent('1 new'))
    expect(spy).not.toHaveBeenCalled()  // not a visible refresh
  })
})

describe('a record sheet', () => {
  it('follows its list: on the list\'s tick it refreshes its own record, the same way ("Up to date" / "Updated: …")', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let status = 'Pending'
    const fetchOne = vi.fn(async () => ({ id: 1, status }))
    function Sheet({ code }: { code: string }) {
      const q = useQuery({ queryKey: ['one', 1], queryFn: fetchOne })
      const r = useRefresh(q, { queryKey: ['one', 1], compare: compareRecord, noChange: 'Up to date', auto: 'follow' })
      return <RecordSheet title={code} refresh={r} tabs={[{ id: 'a', label: 'A', render: () => <p>{q.data?.status}</p> }]} />
    }
    const path = `/follow${Math.random()}`
    localStorage.setItem(`platform-ui.autoRefresh.${path}`, '30')
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
      <ListPage<Row> resource={{ basePath: path, meta: async () => meta, list: async () => ({ items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }) }}
        title="Things" columns={columns} renderDetail={(r) => <Sheet code={r.code} />} />
    </ViewsProvider></QueryClientProvider>)
    fireEvent.click(await screen.findByText('A1'))
    const sheet = await screen.findByRole('dialog', { name: 'A1' })
    expect(await within(sheet).findByText('Pending')).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: /^Refresh · auto every 30s/ })).toBeInTheDocument()  // the list's setting, no menu
    expect(within(sheet).queryByRole('button', { name: 'Auto-refresh' })).toBeNull()
    await advance(30_000)
    await advance(700)
    expect(message(sheet)).toHaveTextContent('Up to date')
    status = 'Shipped'
    await advance(30_000)
    await advance(700)
    expect(message(sheet)).toHaveTextContent('Updated: status')
    expect(fetchOne).toHaveBeenCalledTimes(3)
  })
})

describe('the toolbar: icons with tooltips, and popovers', () => {
  function mountList() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
      <ListPage<Row> resource={{ basePath: `/tb${Math.random()}`, meta: async () => meta, list: async () => ({ items: [{ id: 1, code: 'A1', version: 1 }], total: 1 }) }}
        title="Things" columns={columns} />
    </ViewsProvider></QueryClientProvider>)
  }

  it('Search is an icon opening the advanced search; its badge counts the conditions', async () => {
    mountList()
    await screen.findByText('A1')
    const icon = screen.getByRole('button', { name: 'Search on any field' })
    expect(screen.queryByRole('button', { name: 'Search' })).toBeNull()  // no text button any more
    fireEvent.click(icon)
    const pop = await screen.findByRole('dialog', { name: 'Search on any field' })
    fireEvent.click(within(pop).getByRole('checkbox', { name: 'Code' }))
    fireEvent.change(within(pop).getByLabelText('Code: value'), { target: { value: 'A' } })
    fireEvent.click(within(pop).getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Search on any field (1)' })).toBeInTheDocument())
  })

  it('a changed view: Save as new is an icon opening a popover that saves; Reset is an icon; no "Unsaved changes" text', async () => {
    mountList()
    await screen.findByText('A1')
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'x' } })
    fireEvent.keyDown(screen.getByLabelText('Search'), { key: 'Enter' })
    expect(await screen.findByRole('button', { name: 'Reset to the saved view' })).toBeInTheDocument()
    expect(screen.queryByText('Unsaved changes')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Save as a new view' }))
    const pop = await screen.findByRole('dialog', { name: 'Save as a new view' })
    fireEvent.change(within(pop).getByLabelText('View name'), { target: { value: 'Mine' } })
    fireEvent.click(within(pop).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(views.create).toHaveBeenCalledWith(expect.any(String), 'Mine', expect.objectContaining({ search: 'x' })))
  })
})
