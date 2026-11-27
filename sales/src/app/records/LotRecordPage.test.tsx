import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { PermissionProvider, RecordNavContext, type RecordNav } from '@/platform-ui'
import { hrefOf } from './recordTypes'

// A lot's recall page (stage 3, user 2026-11-27): who got it (customers, orders), how much, delivered or not; the recall
// CSV only for whom the server says may export it (/meta files `allowed`), carrying the lot as the scope.

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), download: vi.fn(async () => {}), file: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: http }))
const { default: LotRecordPage } = await import('./LotRecordPage')

const ROW = { id: 1, lot: 'L1', item_code: 'CF0093', expiry: '2028-04-30', qty_shipped_ea: 60, customer: 'Loblaws', order_ref: 'LCL-1',
  ship_date: '2026-11-20', delivery_confirmed: true, applied: true, shadow: false }

function answer(allowed: boolean) {
  return async (url: string) => {
    const [path, query = ''] = url.split('?')
    const p = new URLSearchParams(query)
    if (path === '/shipped/meta') return { version: 1, fields: [], searchable: false, default_sort: [], refs: { item: { readable: true } },
      capabilities: { export: null, suggest: false, actions: [], files: [{ path: 'recall/customers.csv', media_type: 'text/csv', over: 'list', allowed }] } }
    if (path.endsWith('/meta')) return { version: 1, fields: [], searchable: false, default_sort: [] }
    if (p.getAll('scope').join() !== 'lot:eq:L1') throw new Error(`read without the lot's scope: ${url}`)
    if (path.endsWith('/count')) return { total: 3 }
    if (path === '/shipped') return { items: [ROW], total: 2, totals: [{ currency: null, count: 2, sums: { qty_shipped_ea: '120' } }],
      groups: p.get('group') === 'customer' ? [{ key: ['Loblaws'], count: 2 }] : [{ key: ['LCL-1'], count: 1 }, { key: ['LCL-2'], count: 1 }] }
    if (path === '/inventory/lots') return { items: [{ id: 1, lot: 'L1', on_hand_ea: 0 }], total: 1 }
    return { items: [], total: 0 }
  }
}

const nav: RecordNav = { href: hrefOf, open: () => {}, isOpen: () => false, subscribe: () => () => {} }

function show(allowed: boolean) {
  http.get.mockImplementation(answer(allowed))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><PermissionProvider can={() => true}>
    <RecordNavContext.Provider value={nav}>
      <MemoryRouter initialEntries={['/lots/L1']}><Routes><Route path="/lots/:lot" element={<LotRecordPage />} /></Routes></MemoryRouter>
    </RecordNavContext.Provider>
  </PermissionProvider></QueryClientProvider>)
}

describe('a lot\'s recall page', () => {
  it('who got it, how much; the recall CSV for whom may export it, with the lot as its scope', async () => {
    show(true)
    const page = await screen.findByRole('region', { name: 'L1' })
    expect(await within(page).findByText('120')).toBeInTheDocument()  // shipped EA
    expect(within(page).getByRole('link', { name: 'CF0093' })).toHaveAttribute('href', '/items/CF0093')
    expect(within(page).getByText('2 orders')).toBeInTheDocument()
    expect(within(page).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Customers2', 'All 945 rows3', 'Stock card3'])
    fireEvent.click(await within(page).findByRole('button', { name: 'Export for a recall (CSV)' }))
    await waitFor(() => expect(http.download).toHaveBeenCalled())
    const [path, name] = http.download.mock.calls[0] as unknown as [string, string]
    expect(path).toBe('/shipped/recall/customers.csv?scope=lot%3Ain%3AL1')
    expect(name).toBe('recall-L1.csv')
    expect(within(page).getByRole('note', { name: 'Scope: Lot = L1' })).toBeInTheDocument()  // its Customers list, locked
  })

  it('not held: no export offered (the server refuses it too)', async () => {
    show(false)
    const page = await screen.findByRole('region', { name: 'L1' })
    await within(page).findByText('120')
    expect(within(page).queryByRole('button', { name: 'Export for a recall (CSV)' })).toBeNull()
  })
})
