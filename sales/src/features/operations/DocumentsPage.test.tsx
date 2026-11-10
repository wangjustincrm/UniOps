import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { RouterOpenRecord } from '@/app/RouterOpenRecord'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { DocumentRead } from './operationsApi'

const base: DocumentRead = {
  id: 1, direction: 'IN', origin: 'PARTNER', system: 'RADIANT', connection_id: 7, kind: '945_EDI', remote_name: 'a.945',
  size: 120, remote_mtime: '2026-10-29T11:00:00Z', sha256: 'ab'.repeat(32), has_content: true, processing_status: 'Pending',
  file_status: null, ack_status: null, isa13: '000000001', gs06: '456', st02: '0001', partner_code: null, error: null,
  created_at: '2026-10-29T12:00:00Z', created_by_name: 'SYSTEM', shipments_without_order: 0,
  orders: [{ id: 11, label: 'SHP-9567' }], orders_total: 1, transfers: [], no_order_reason: null,
}
const dup: DocumentRead = { ...base, id: 2, remote_name: 'b.945', has_content: false, processing_status: 'Duplicate', error: 'same bytes as document 1',
  orders: [], orders_total: 0, no_order_reason: 'Duplicate of a file already read' }
const page: DocumentRead = { ...base, id: 3, kind: 'SHOPIFY_ORDER_JSON', remote_name: 'orders page 1',
  orders: [{ id: 21, label: 'SHP-9601' }, { id: 22, label: 'SHP-9602' }, { id: 23, label: 'SHP-9603' }], orders_total: 3 }
vi.mock('./operationsApi', async (orig) => {
  const real = await orig<typeof import('./operationsApi')>()
  return { ...real, documentsClient: { ...real.documentsClient, list: vi.fn(async () => ({ items: [base, dup, page], total: 3 })) } }
})
const { default: DocumentsPage } = await import('./DocumentsPage')

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={() => true}>
    <MemoryRouter initialEntries={['/documents']}><RouterOpenRecord><Routes>
      <Route path="/documents" element={<DocumentsPage />} />
      <Route path="/orders" element={<p>Orders page</p>} />
    </Routes></RouterOpenRecord></MemoryRouter>
  </PermissionProvider></ToastProvider></QueryClientProvider>)
}

describe('Documents page', () => {
  it('offers the original bytes only where the record keeps them', async () => {
    renderPage()
    fireEvent.click(await screen.findByText('a.945'))
    let sheet = await screen.findByRole('dialog')
    expect(within(sheet).getByText('Download original')).toBeInTheDocument()
    fireEvent.keyDown(sheet, { key: 'Escape' })
    fireEvent.click(await screen.findByText('b.945'))
    sheet = await screen.findByRole('dialog')
    expect(within(sheet).queryByText('Download original')).not.toBeInTheDocument()
    expect(within(sheet).getAllByText('same bytes as document 1').length).toBeGreaterThan(0)
  })

  it('shows the orders of each file: the first two, "+N more" for all, and why when there is none', async () => {
    renderPage()
    expect(await screen.findByText('Duplicate of a file already read')).toBeInTheDocument()
    expect(screen.getByText('SHP-9601')).toBeInTheDocument()
    expect(screen.queryByText('SHP-9603')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '1 more order' }))
    expect(await screen.findByText('3 orders')).toBeInTheDocument()
    expect(screen.getAllByText('SHP-9603').length).toBe(1)
    expect(screen.queryByRole('dialog', { name: /orders page 1/ })).not.toBeInTheDocument()  // the row did not open
  })

  it('a ref opens its order, not the document', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'SHP-9567' }))
    expect(await screen.findByText('Orders page')).toBeInTheDocument()
  })
})
