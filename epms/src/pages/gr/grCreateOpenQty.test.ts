import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { QueryClient, QueryObserver } from '@tanstack/react-query'

/**
 * ISS-0226: after Cancel GR, New GR (same session, no reload) still showed the
 * cancelled quantity as Pending. Cancel invalidates ['grs', …], but New GR is
 * not mounted then, so its open-quantities query only goes stale — and the
 * prefill ran on the stale cached figures (isFetched is already true for
 * cached data) and then locked, ignoring the refetch.
 */
describe('New GR open quantities after a GR is cancelled (ISS-0226)', () => {
  it('cached figures count as fetched, but not as fetched after mount', async () => {
    const client = new QueryClient()
    const key = ['grs', 'open-quantities', 'po-1']
    client.setQueryData(key, [{ pending: 5, open: 0 }])        // before the cancel
    await client.invalidateQueries({ queryKey: ['grs'] })      // useGrAction onSuccess

    const observer = new QueryObserver(client, {
      queryKey: key,
      queryFn: async () => [{ pending: 0, open: 5 }],          // the server after the cancel
    })
    const first = observer.getCurrentResult()
    expect(first.isFetched).toBe(true)
    expect(first.isFetchedAfterMount).toBe(false)

    const fresh = await new Promise<ReturnType<typeof observer.getCurrentResult>>((resolve) => {
      const unsubscribe = observer.subscribe((r) => {
        if (r.isFetchedAfterMount) { unsubscribe(); resolve(r) }
      })
    })
    expect(fresh.data).toEqual([{ pending: 0, open: 5 }])
    client.clear()
  })

  it('the New GR prefill waits for figures fetched after mount', () => {
    const src = fs.readFileSync(path.join(__dirname, 'GrCreatePage.tsx'), 'utf8')
    expect(src).toMatch(/if \(!openQtyQuery\.isFetchedAfterMount\) return/)
    expect(src).not.toMatch(/openQtyQuery\.isFetched\b(?!AfterMount)/)
  })
})
