import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { ActionButton, DownloadButton, NoticeScope, RowActions } from './index'

// One action runner (2026-11-10): a row action runs once for two clicks in one tick, and a download
// failure is said in the status row it is in, with its prefix.
describe('useAction', () => {
  it('a row action clicked twice in one tick runs once', async () => {
    let finish!: () => void
    const run = vi.fn(() => new Promise<void>((r) => { finish = r }))
    render(<RowActions actions={[{ key: 'go', label: 'Go', icon: <span>g</span>, onAction: run }]} />)
    const b = screen.getByRole('button', { name: 'Go' })
    fireEvent.click(b); fireEvent.click(b)
    expect(run).toHaveBeenCalledTimes(1)
    await act(async () => { finish() })
  })

  it('a download that fails says so in its status row', async () => {
    render(<NoticeScope><DownloadButton onAction={() => Promise.reject(new Error('no file'))}>PO</DownloadButton></NoticeScope>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /PO/ })) })
    expect(await screen.findByText(/Download failed: no file/)).toBeInTheDocument()
  })

  it('an action that worked reads again what is on screen; a download does not', async () => {
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    render(<QueryClientProvider client={qc}>
      <ActionButton onAction={async () => 'ok'}>Approve</ActionButton>
      <DownloadButton onAction={async () => undefined}>PDF</DownloadButton>
    </QueryClientProvider>)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Approve' })) })
    expect(spy).toHaveBeenCalledTimes(1)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /PDF/ })) })
    expect(spy).toHaveBeenCalledTimes(1)
  })
})
