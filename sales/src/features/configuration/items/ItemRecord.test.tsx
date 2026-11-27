import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, RecordContainerContext } from '@/platform-ui'
import { openTab } from '@/platform-ui/testing'

// An item's record (stage 2, user 2026-11-27), here in its own tab: its facts first, the edit form behind "Edit item"; each list that
// concerns it a tab, counted on the server and read inside the item's scope (?scope=item_code:eq:…).

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), download: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: http }))
const { ItemRecord } = await import('./ItemRecord')

const ITEM = { id: 4, item_code: 'CF0093', description: 'Stage 3', sku: 'S3', upc: null, gtin: null, gross_weight_lb_per_ea: '2.1',
  warehouse_held: true, units: [{ unit: 'EA', factor_ea: '1.0000', shopify_titles: [] }, { unit: 'CASE', factor_ea: '6.0000', shopify_titles: [] }],
  active: true, version: 3, created_at: '2026-11-01T00:00:00Z', created_by_name: 'A', updated_at: null, updated_by_name: null }
const STOCK = { id: 1, item_code: 'CF0093', on_hand_ea: 1212, available_ea: 672, allocated_ea: 540, hold_ea: 0, first_expiry: '2028-04-30', days_to_first_expiry: 520 }
const COUNTS: Record<string, number> = { '/orders': 64, '/invoices': 29, '/transfers': 4, '/shipped': 77, '/inventory/lots': 4, '/inventory/moves': 188, '/config/partner-codes': 3 }

http.get.mockImplementation(async (url: string) => {
  const [path, query = ''] = url.split('?')
  const p = new URLSearchParams(query)
  if (path.endsWith('/meta')) return { version: 1, fields: [], searchable: false, default_sort: [] }
  if (path.endsWith('/count')) {
    if (p.getAll('scope').join() !== 'item_code:eq:CF0093') throw new Error(`count without the scope: ${url}`)
    return { total: COUNTS[path.slice(0, -'/count'.length)] }
  }
  if (path === '/config/items') return { items: [ITEM], total: 1 }
  if (path === '/inventory/items') return { items: [STOCK], total: 1 }
  if (path === '/orders') return { items: [], total: 0 }
  return { items: [], total: 0 }
})

function show(keys = ['sales.read', 'sales.config']) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><PermissionProvider can={(k) => keys.includes(k)}>
    <RecordContainerContext.Provider value={{ kind: 'tab', close: () => {} }}><ItemRecord row={ITEM} close={() => {}} /></RecordContainerContext.Provider>
  </PermissionProvider></QueryClientProvider>)
}

describe('an item\'s record', () => {
  it('its facts first, its details read-only; the form only behind Edit item', async () => {
    show()
    const sheet = await screen.findByRole('region', { name: 'CF0093' })
    expect(await within(sheet).findByText('1,212')).toBeInTheDocument()  // on hand, the first fact
    expect(within(sheet).getByText('672')).toBeInTheDocument()
    expect(within(sheet).getByText('EA=1; CASE=6')).toBeInTheDocument()
    expect(within(sheet).queryByRole('textbox')).toBeNull()  // no form until asked
    fireEvent.click(within(sheet).getByRole('button', { name: 'Edit item' }))
    expect(await within(sheet).findByLabelText(/^Description/)).toHaveValue('Stage 3')
    expect(within(sheet).queryByRole('button', { name: 'Edit item' })).toBeNull()
    fireEvent.click(within(sheet).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(within(sheet).queryByRole('textbox')).toBeNull())
  })

  it('a reader sees no Edit item', async () => {
    show(['sales.read'])
    const sheet = await screen.findByRole('region', { name: 'CF0093' })
    expect(within(sheet).queryByRole('button', { name: 'Edit item' })).toBeNull()
  })

  it('every list that concerns it is a tab, counted on the server inside the item\'s scope', async () => {
    show()
    const sheet = await screen.findByRole('region', { name: 'CF0093' })
    await waitFor(() => expect(within(sheet).getByRole('tab', { name: /^Orders/ })).toHaveTextContent('64'))
    const names = within(sheet).getAllByRole('tab').map((t) => t.textContent)
    expect(names).toEqual(['Overview', 'Lots4', 'Stock card188', 'Shipped77', 'Orders64', 'Invoices29', 'Transfers4', 'Partner codes3', 'History'])
    await openTab(sheet, 'Orders')
    await waitFor(() => expect(http.get.mock.calls.map(([u]) => u as string).some((u) => u.startsWith('/orders?') && u.includes('scope=item_code%3Aeq%3ACF0093'))).toBe(true))
    expect(await within(sheet).findByRole('note', { name: 'Scope: Item = CF0093' })).toBeInTheDocument()
  })
})
