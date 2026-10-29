import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { RunRead } from './operationsApi'

const run: RunRead = {
  id: 12, job: 'sales_cycle', trigger: 'MANUAL', status: 'PARTIAL', started_at: '2026-10-29T12:00:00Z',
  heartbeat_at: '2026-10-29T12:00:05Z', finished_at: '2026-10-29T12:00:06Z', error: null,
  summary: { connections: {
    radiant: { listed: 4, too_new: 0, already_here: 1, not_ours: 1, stored: 1, duplicates: 1, failed: 0, status: 'ok' },
    truecommerce: { listed: 0, too_new: 0, already_here: 0, not_ours: 0, stored: 0, duplicates: 0, failed: 0, status: 'list_failed' },
  }, outbox: {} },
}
const api = vi.hoisted(() => ({ runNow: vi.fn() }))
vi.mock('./operationsApi', async (orig) => {
  const real = await orig<typeof import('./operationsApi')>()
  return {
    ...real,
    runsClient: { ...real.runsClient, list: vi.fn(async () => ({ items: [run], total: 1 })) },
    logClient: { ...real.logClient, list: vi.fn(async () => ({ items: [], total: 0 })) },
    runNow: api.runNow,
  }
})
const { default: RunsPage } = await import('./RunsPage')

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}><ToastProvider>
      <PermissionProvider can={(k) => keys.includes(k)}><RunsPage /></PermissionProvider>
    </ToastProvider></QueryClientProvider>,
  )
}

describe('Runs page', () => {
  it('Run now needs sales.file.retry', async () => {
    const { unmount } = renderAs(['sales.read'])
    expect(await screen.findByText('PARTIAL', { selector: 'span' })).toBeInTheDocument()
    expect(screen.queryByText('Run now')).not.toBeInTheDocument()
    unmount()
    api.runNow.mockResolvedValue({ run_id: 13 })
    renderAs(['sales.read', 'sales.file.retry'])
    fireEvent.click(await screen.findByText('Run now'))
    await waitFor(() => expect(api.runNow).toHaveBeenCalledOnce())
  })

  it('a run shows its pass report per connection', async () => {
    renderAs(['sales.read'])
    fireEvent.click(await screen.findByText('PARTIAL', { selector: 'span' }))  // the badge, not the filter option
    const report = await within(await screen.findByRole('dialog')).findByRole('region', { name: 'Pass report' })
    const rows = within(report).getAllByRole('row').slice(1).map((r) => r.textContent)
    expect(rows).toEqual(['radiant4011110ok', 'truecommerce0000000list_failed'])
  })
})
