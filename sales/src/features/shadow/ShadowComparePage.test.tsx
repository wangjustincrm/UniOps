import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { ShadowCompare, ShadowMatchRead } from './shadowApi'

// Operations → Shadow compare (§5.4): counts per status, both files side by
// side with the differing segments marked, and "expected difference".

const differs: ShadowMatchRead = {
  id: 3, kind: '940', status: 'Differs', reference_document_id: 40, order_id: 23, reference_number: '9471', partner_code: 'Shopify',
  differences: 1, expected_reason: null, note: null, compared_at: '2026-11-02T14:00:00Z', version: 2, updated_by_name: null,
}
const match: ShadowMatchRead = { ...differs, id: 4, status: 'Match', reference_number: '9473', differences: 0 }
const view: ShadowCompare = {
  ...differs, reference_name: '940s/z1.edi', ignored: ['ISA ISA09, ISA10, ISA13'],
  rows: [{ zoho: 'W05*N*9471', sales: 'W05*N*9471', same: true },
    { zoho: 'W01*18*EA**VN*CF0092', sales: 'W01*12*EA**VN*CF0092', same: false }],
}

const api = vi.hoisted(() => ({ compare: vi.fn(), mark: vi.fn(), summary: vi.fn() }))
vi.mock('./shadowApi', async (orig) => {
  const real = await orig<typeof import('./shadowApi')>()
  return {
    ...real,
    shadowClient: { ...real.shadowClient, list: vi.fn(async () => ({ items: [differs, match], total: 2 })) },
    fetchShadowCompare: api.compare, markExpected: api.mark, fetchShadowSummary: api.summary,
  }
})
const { default: ShadowComparePage } = await import('./ShadowComparePage')

function renderAs(keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider>
    <PermissionProvider can={(k) => keys.includes(k)}><ShadowComparePage /></PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Shadow compare', () => {
  beforeEach(() => {
    api.compare.mockReset(); api.compare.mockResolvedValue(view)
    api.mark.mockReset(); api.summary.mockReset()
    api.summary.mockResolvedValue({ Match: 1, Differs: 1, 'Expected difference': 0, 'No Sales order': 0, 'Sales only': 0, Unreadable: 0 })
  })

  it('shows the counts per status and both files side by side, the differing segment marked', async () => {
    renderAs(['sales.read'])
    const summary = await screen.findByRole('list', { name: 'Comparison summary' })
    expect(within(summary).getByText('Differs').closest('li')).toHaveTextContent('Differs1')
    fireEvent.click(await screen.findByText('9471'))
    const dialog = await screen.findByRole('dialog')
    const table = await within(dialog).findByRole('table', { name: 'Segments' })
    const changed = within(table).getByText('W01*18*EA**VN*CF0092').closest('tr')!
    expect(changed).toHaveAttribute('data-differs', 'true')
    expect(within(changed).getByText('W01*12*EA**VN*CF0092')).toBeInTheDocument()
    expect(within(table).getAllByText('W05*N*9471')[0].closest('tr')).not.toHaveAttribute('data-differs')
    expect(within(dialog).getByText('ISA ISA09, ISA10, ISA13')).toBeInTheDocument()
    expect(within(dialog).queryByText('Mark as expected difference')).toBeNull()  // a reader cannot mark
  })

  it('marks an expected difference with a reason (sales.order.operate)', async () => {
    api.mark.mockResolvedValue({ ...differs, status: 'Expected difference', expected_reason: 'Zoho rounds up' })
    renderAs(['sales.read', 'sales.order.operate'])
    fireEvent.click(await screen.findByText('9471'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Mark as expected difference' }))
    const ask = await screen.findByRole('dialog', { name: 'Mark these differences as expected?' })
    const button = within(ask).getByRole('button', { name: 'Mark as expected' })
    expect(button).toBeDisabled()  // a reason first
    fireEvent.change(within(ask).getByLabelText(/Why these differences are expected/), { target: { value: 'Zoho rounds up' } })
    fireEvent.click(button)
    await waitFor(() => expect(api.mark).toHaveBeenCalledWith(3, 'Zoho rounds up', 2))
  })
})
