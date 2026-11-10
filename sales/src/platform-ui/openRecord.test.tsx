import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ListPage, OpenRecordProvider, RecordSheet, RecordSheetHostContext, ViewsProvider, useOpenRecord, type ColumnDef } from './index'
import { useReportUnsaved } from './primitives/unsaved'
import { requestOpen, takeOpenRequest } from './resource/viewRequests'
import type { ViewsClient } from './resource/viewsApi'

// One way to another record (2026-11-10): it closes the sheet it is called from, asks about unsaved
// input there first, and only the resource's own list (not one with fixed filters) takes the request.
afterEach(() => { vi.restoreAllMocks(); takeOpenRequest('/things') })

function Opener({ dirty = false }: { dirty?: boolean }) {
  useReportUnsaved(dirty)
  const open = useOpenRecord()
  return <button type="button" onClick={() => open('/things', 7)}>open thing 7</button>
}

describe('useOpenRecord', () => {
  it('closes the record sheet it is called from, asks the list and goes there', () => {
    const close = vi.fn(); const go = vi.fn()
    render(<OpenRecordProvider navigate={go}><RecordSheetHostContext.Provider value={{ open: true, close }}><Opener /></RecordSheetHostContext.Provider></OpenRecordProvider>)
    fireEvent.click(screen.getByText('open thing 7'))
    expect(close).toHaveBeenCalled()
    expect(go).toHaveBeenCalledWith('/things')
    expect(takeOpenRequest('/things')).toEqual({ id: 7 })
  })

  it('stays, sheet open and nothing asked, when the person keeps unsaved input', () => {
    const close = vi.fn(); const go = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<OpenRecordProvider navigate={go}><RecordSheetHostContext.Provider value={{ open: true, close }}><Opener dirty /></RecordSheetHostContext.Provider></OpenRecordProvider>)
    fireEvent.click(screen.getByText('open thing 7'))
    expect(close).not.toHaveBeenCalled()
    expect(go).not.toHaveBeenCalled()
    expect(takeOpenRequest('/things')).toBeUndefined()
  })
})

describe('ListPage and open requests', () => {
  type Row = { id: number; code: string }
  const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }]
  const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }
  const resource = {
    basePath: '/things', list: vi.fn(async () => ({ items: [{ id: 7, code: 'T7' }], total: 1 })),
    meta: async () => ({ version: 1, searchable: false, default_sort: [], fields: [], views: [] }),
  }
  it('a list with fixed filters (inside another page) leaves the request to the resource own list', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
      <ListPage<Row> resource={resource} columns={columns} fixed={[{ field: 'code', op: 'eq', value: 'T7' }]}
        renderDetail={(r) => <RecordSheet title={`Thing ${r.code}`} tabs={[]} />} />
    </ViewsProvider></QueryClientProvider>)
    await screen.findByText('T7')
    act(() => { requestOpen('/things', 7) })
    await waitFor(() => expect(resource.list).toHaveBeenCalled())
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(takeOpenRequest('/things')).toEqual({ id: 7 })  // still there for the Things page
  })
})
