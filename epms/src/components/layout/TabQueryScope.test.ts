import { describe, it, expect, vi } from 'vitest'
import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { createTabClient } from './TabQueryScope'

// ISS-0131: a tab's Refresh re-reads the queries that tab asked for, and only
// those; the data itself stays in the one shared cache.
describe('createTabClient', () => {
  it('refreshes only the queries observed through the tab client', async () => {
    const root = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { client, refresh } = createTabClient(root)
    const fetchMine = vi.fn(async () => 'mine')
    const fetchOther = vi.fn(async () => 'other')

    const mine = new QueryObserver(client, { queryKey: ['mine'], queryFn: fetchMine })
    const other = new QueryObserver(root, { queryKey: ['other'], queryFn: fetchOther })
    const un1 = mine.subscribe(() => {})
    const un2 = other.subscribe(() => {})
    await vi.waitFor(() => expect(root.getQueryData(['mine'])).toBe('mine'))
    await vi.waitFor(() => expect(root.getQueryData(['other'])).toBe('other'))
    expect(fetchMine).toHaveBeenCalledTimes(1)

    await refresh()
    expect(fetchMine).toHaveBeenCalledTimes(2)
    expect(fetchOther).toHaveBeenCalledTimes(1)

    // onlyStale (tab return) skips a query that is still fresh.
    const fresh = new QueryObserver(client, { queryKey: ['fresh'], queryFn: fetchMine, staleTime: 60_000 })
    const un3 = fresh.subscribe(() => {})
    await vi.waitFor(() => expect(root.getQueryData(['fresh'])).toBe('mine'))
    const before = fetchMine.mock.calls.length
    await refresh({ onlyStale: true })
    expect(fetchMine.mock.calls.length).toBe(before + 1) // ['mine'] only, not ['fresh']

    un1(); un2(); un3()
  })
})
