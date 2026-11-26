import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ListPage, PageActivityContext, RecordSheet, ViewsProvider, type ColumnDef } from './index'
import type { ListMeta } from './resource/listMeta'
import type { ViewsClient } from './resource/viewsApi'

// A page kept alive in an app tab that is not shown (user 2026-11-26): no auto-refresh tick there; shown again, what is older
// than 30 s is read once at once (the old rows stay while reading; the status row says what changed), fresher nothing is read;
// what is typed in it stays.

type Row = { id: number; code: string; version: number }
const meta: ListMeta = { version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }]
const held: { control: (active: boolean) => void } = { control: () => {} }

function Tab({ children }: { children: React.ReactNode }) {
  const [active, setActive] = useState(true)
  useEffect(() => { held.control = setActive }, [])
  return <PageActivityContext.Provider value={{ active }}>{children}</PageActivityContext.Provider>
}

function mount(auto: number) {
  const path = `/kept${Math.random()}`
  if (auto) localStorage.setItem(`platform-ui.autoRefresh.${path}`, String(auto))
  let n = 0
  const list = vi.fn(async () => {
    if (n > 0) await new Promise((ok) => setTimeout(ok, 300))       // a read that takes a moment: the old rows stay meanwhile
    return { items: [{ id: 1, code: `A${n}`, version: ++n }], total: 1 }
  })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ViewsProvider client={views}><Tab>
    <ListPage<Row> resource={{ basePath: path, meta: async () => meta, list }} title="Things" columns={columns}
      renderDetail={(r) => <RecordSheet title={`Thing ${r.code}`} tabs={[{ id: 'd', label: 'Detail', render: () => <input aria-label="Note" /> }]} />} />
  </Tab></ViewsProvider></QueryClientProvider>)
  return list
}
const at = (ms: number) => act(async () => { vi.advanceTimersByTime(ms) })

afterEach(() => { vi.useRealTimers() })

describe('a page in an app tab that is not shown', () => {
  it('does not tick; shown again after a while, it is read once at once, then ticks again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const list = mount(30)
    await screen.findByText('A0')
    const first = list.mock.calls.length
    act(() => held.control(false))                                      // another tab is shown
    await at(95_000)                                               // three ticks would have run
    expect(list.mock.calls.length).toBe(first)
    act(() => held.control(true))                                       // shown again: older than 30 s
    await waitFor(() => expect(list.mock.calls.length).toBe(first + 1))
    expect(screen.getByText('A0')).toBeInTheDocument()             // the old row stays while reading
    await screen.findByText('A1')
    await waitFor(() => expect(document.querySelector('[data-status-bar] [data-tone]')).toHaveTextContent('1 updated'))
    await at(20_000)
    expect(list.mock.calls.length).toBe(first + 1)                 // the tick waits its full time from here
    await at(11_000)
    await waitFor(() => expect(list.mock.calls.length).toBe(first + 2))
  })

  it('shown again soon (fresher than 30 s): nothing is read; with no auto-refresh, still read when old', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const list = mount(0)
    await screen.findByText('A0')
    const first = list.mock.calls.length
    act(() => held.control(false))
    await at(10_000)
    act(() => held.control(true))
    await at(1_000)
    expect(list.mock.calls.length).toBe(first)
    act(() => held.control(false))
    await at(40_000)
    expect(list.mock.calls.length).toBe(first)                     // no auto: nothing while away
    act(() => held.control(true))
    await waitFor(() => expect(list.mock.calls.length).toBe(first + 1))
  })

  it('what is typed in its open record stays when it is read again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const list = mount(30)
    fireEvent.click(await screen.findByText('A0'))
    const sheet = await screen.findByRole('dialog', { name: 'Thing A0' })
    fireEvent.change(within(sheet).getByLabelText('Note'), { target: { value: 'typed, not saved' } })
    const first = list.mock.calls.length
    act(() => held.control(false))
    await at(60_000)
    act(() => held.control(true))
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(first))
    expect(screen.getByLabelText('Note')).toHaveValue('typed, not saved')
  })
})
