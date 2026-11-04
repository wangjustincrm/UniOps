import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PermissionProvider, ToastProvider } from '@/platform-ui'
import type { DocumentRead } from './operationsApi'

const base: DocumentRead = {
  id: 1, direction: 'IN', origin: 'PARTNER', system: 'RADIANT', connection_id: 7, kind: '945_EDI', remote_name: 'a.945',
  size: 120, remote_mtime: '2026-10-29T11:00:00Z', sha256: 'ab'.repeat(32), has_content: true, processing_status: 'Pending',
  file_status: null, ack_status: null, isa13: '000000001', gs06: '456', st02: '0001', partner_code: null, error: null,
  created_at: '2026-10-29T12:00:00Z', created_by_name: 'SYSTEM', shipments_without_order: 0,
}
const dup: DocumentRead = { ...base, id: 2, remote_name: 'b.945', has_content: false, processing_status: 'Duplicate', error: 'same bytes as document 1' }
vi.mock('./operationsApi', async (orig) => {
  const real = await orig<typeof import('./operationsApi')>()
  return { ...real, documentsClient: { ...real.documentsClient, list: vi.fn(async () => ({ items: [base, dup], total: 2 })) } }
})
const { default: DocumentsPage } = await import('./DocumentsPage')

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ToastProvider><PermissionProvider can={() => true}><DocumentsPage /></PermissionProvider></ToastProvider></QueryClientProvider>)
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
})
