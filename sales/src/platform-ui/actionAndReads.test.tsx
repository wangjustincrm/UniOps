import { type ReactNode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ActionButton, ConfirmAction, NoticeScope, QueryState, compareRecord, queryKeys, useLookup, useRecord, useRefresh } from './index'

// The one way pages act and read (step 1, user 2026-11-18): an action's result-dependent notice and its `after`,
// a targeted refresh, ConfirmAction's success said like a button's; reads keyed by the resource, off without an id.

const wrap = (qc: QueryClient, node: ReactNode) => render(<QueryClientProvider client={qc}><NoticeScope>{node}</NoticeScope></QueryClientProvider>)

describe('useAction: done by result, after, invalidates', () => {
  it('says a warning when the result says so, then runs after', async () => {
    const after = vi.fn()
    wrap(new QueryClient(), <ActionButton onAction={async () => ({ remaining: 2 })} after={after}
      done={(r) => ((r as { remaining: number }).remaining ? { tone: 'warning', text: 'still 2 problems' } : 'all clear')}>Re-check</ActionButton>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Re-check' })) })
    expect(await screen.findByText('still 2 problems')).toBeInTheDocument()
    expect(after).toHaveBeenCalledWith({ remaining: 2 })
  })

  it('reads again only what it names when it names something', async () => {
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    const key = queryKeys.detail({ basePath: '/things' }, 7)
    wrap(qc, <ActionButton onAction={async () => 'ok'} invalidates={[key]}>Go</ActionButton>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Go' })) })
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith({ queryKey: key })
  })

  it('ConfirmAction: a success is said and refreshed like a button; a failure stays in the dialog', async () => {
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    const after = vi.fn()
    let fail = true
    wrap(qc, <ConfirmAction open onOpenChange={() => {}} title="Cancel the order?" done="Order cancelled." after={after}
      onConfirm={async () => { if (fail) throw new Error('nope') }} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm' })) })
    expect(screen.getByRole('alert')).toHaveTextContent('nope')
    expect(spy).not.toHaveBeenCalled()
    fail = false
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm' })) })
    expect(await screen.findByText('Order cancelled.')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(after).toHaveBeenCalledTimes(1)
  })
})

describe('an action whose record had moved (entity.stale)', () => {
  const moved = { body: { error: { code: 'entity.stale', message: 'this order was changed by someone else; reload it', details: {} } } }
  const things = { basePath: '/things' }

  it('a confirmation keeps what was typed, reads the record again, says what changed, and the next press acts on the new one', async () => {
    let server = { id: 1, version: 4, status: 'Sent' }
    const get = vi.fn(async () => server)
    const send = vi.fn(async (version: number, why: string) => { if (version !== server.version) throw Object.assign(new Error('409'), moved); return why })
    function Sheet() {
      const q = useRecord(things, 1, get)
      const r = q.data
      return r ? <ConfirmAction open onOpenChange={() => {}} title="Replace?" reason={{ label: 'Why' }} record={r}
        invalidates={[queryKeys.detail(things, 1)]} onConfirm={(why) => send(r.version, why)} /> : null
    }
    wrap(new QueryClient(), <Sheet />)
    fireEvent.change(await screen.findByLabelText(/Why/), { target: { value: 'wrong quantity' } })
    server = { id: 1, version: 5, status: 'Accepted' }
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm' })) })
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/changed by someone else.*Updated: .*status/i))
    expect(screen.getByLabelText(/Why/)).toHaveValue('wrong quantity')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm' })) })
    expect(send.mock.calls).toEqual([[4, 'wrong quantity'], [5, 'wrong quantity']])
  })

  it('a button reads again what it names; another failure reads nothing', async () => {
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    const key = queryKeys.detail(things, 7)
    let error: Error = new Error('nope')
    wrap(qc, <ActionButton onAction={async () => { throw error }} invalidates={[key]}>Go</ActionButton>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Go' })) })
    expect(spy).not.toHaveBeenCalled()
    error = Object.assign(new Error('409'), moved)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Go' })) })
    expect(spy).toHaveBeenCalledWith({ queryKey: key })
  })
})

describe('useRecord / useLookup', () => {
  const things = { basePath: '/things' }
  it('fetches nothing without an id, then the record under its detail key', async () => {
    const get = vi.fn(async (id: number) => ({ id, name: `T${id}` }))
    function Show() {
      const [id, setId] = useState<number | null>(null)
      const q = useRecord(things, id, get)
      return <><button type="button" onClick={() => setId(3)}>pick</button><p>{q.data?.name ?? 'none'}</p></>
    }
    const qc = new QueryClient()
    wrap(qc, <Show />)
    expect(get).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'pick' }))
    expect(await screen.findByText('T3')).toBeInTheDocument()
    expect(qc.getQueryData(queryKeys.detail(things, 3))).toEqual({ id: 3, name: 'T3' })
  })

  it('a record with auto-refresh and an action: the action reads it again once, not twice', async () => {
    const get = vi.fn(async (id: number) => ({ id, n: get.mock.calls.length }))
    function Sheet() {
      const q = useRecord(things, 1, get)
      useRefresh(q, { queryKey: queryKeys.detail(things, 1), compare: compareRecord, auto: 300 })
      return <><p>n={q.data?.n ?? '-'}</p><ActionButton onAction={async () => 'ok'}>Act</ActionButton></>
    }
    wrap(new QueryClient(), <Sheet />)
    await screen.findByText('n=1')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Act' })) })
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
    await new Promise((r) => setTimeout(r, 50))
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('a lookup without a key fetches nothing', async () => {
    const fetch = vi.fn(async () => 1)
    function L({ k }: { k: string | null }) { useLookup(k ? ['x', k] : null, fetch); return null }
    const qc = new QueryClient()
    const { rerender } = wrap(qc, <L k={null} />)
    expect(fetch).not.toHaveBeenCalled()
    rerender(<QueryClientProvider client={qc}><NoticeScope><L k="a" /></NoticeScope></QueryClientProvider>)
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
  })
})

describe('a failed read, the same everywhere', () => {
  const things = { basePath: '/things' }
  it('is said in the status row with Retry, and cleared once it reads', async () => {
    let fail = true
    const get = vi.fn(async (id: number) => { if (fail) throw new Error('server down'); return { id } })
    function Show() { const q = useRecord(things, 1, get); return <p>{q.data ? 'loaded' : 'waiting'}</p> }
    wrap(new QueryClient({ defaultOptions: { queries: { retry: false } } }), <Show />)
    expect(await screen.findByText(/Could not load: server down/)).toBeInTheDocument()
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('loaded')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(/Could not load/)).toBeNull())
  })

  it('QueryState: a skeleton, then the error with Retry where the content goes (inline, not in the row)', async () => {
    const fetch = vi.fn(async () => { throw new Error('nope') })
    function Show() {
      const q = useLookup(['x'], fetch, { errors: 'inline' })
      return <QueryState q={q}>{() => <p>content</p>}</QueryState>
    }
    wrap(new QueryClient({ defaultOptions: { queries: { retry: false } } }), <Show />)
    expect(await screen.findByText('nope')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.queryByText(/Could not load/)).toBeNull()
  })

  it('a record with no way to read it says so in development', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    function Show() { useRecord({ basePath: '/nothing' } as never, 1, undefined as never); return null }
    wrap(new QueryClient(), <Show />)
    expect(err).toHaveBeenCalledWith(expect.stringContaining('/nothing'))
    err.mockRestore()
  })
})
