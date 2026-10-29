// Regression (dev2, 2026-10-29): a list that failed while sales-api was down
// stayed on "Failed to fetch" in its keep-alive tab and never asked again.
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { refetchOnWindowFocus, useRecoverFailedQueries } from './recoverFailedQueries'

function Page({ name, fetcher }: { name: string; fetcher: () => Promise<string> }) {
  const q = useQuery({ queryKey: [name], queryFn: fetcher })
  return <p>{name}: {q.isError ? 'failed' : q.data ?? 'loading'}</p>
}

function Shell({ activeKey, pages }: { activeKey: string; pages: React.ReactNode }) {
  useRecoverFailedQueries(activeKey)
  return <>{pages}</>
}

describe('failed queries recover', () => {
  it('a failed list refetches when its tab is shown again; a healthy one does not', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus } } })
    const partners = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValue('7 rows')
    const documents = vi.fn().mockResolvedValue('4 rows')
    const pages = <><Page name="partners" fetcher={partners} /><Page name="documents" fetcher={documents} /></>
    const { rerender } = render(<QueryClientProvider client={qc}><Shell activeKey="/config/partners" pages={pages} /></QueryClientProvider>)
    expect(await screen.findByText('partners: failed')).toBeInTheDocument()
    expect(await screen.findByText('documents: 4 rows')).toBeInTheDocument()

    rerender(<QueryClientProvider client={qc}><Shell activeKey="/documents" pages={pages} /></QueryClientProvider>)
    expect(await screen.findByText('partners: 7 rows')).toBeInTheDocument()
    await waitFor(() => expect(partners).toHaveBeenCalledTimes(2))
    expect(documents).toHaveBeenCalledTimes(1)  // healthy: not asked again
  })

  it('window focus refetches only what failed', () => {
    const q = (status: string) => ({ state: { status } }) as never
    expect(refetchOnWindowFocus(q('error'))).toBe(true)
    expect(refetchOnWindowFocus(q('success'))).toBe(false)
  })
})
