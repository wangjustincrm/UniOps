import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { ListPage, ViewsProvider, type ColumnDef } from './index'
import type { ViewsClient } from './resource/viewsApi'

// Paging follows the filter (user 2026-11-11: "when we filter there is no change for paging").
type Row = { id: number; code: string }
const all: Row[] = Array.from({ length: 120 }, (_, i) => ({ id: i + 1, code: i < 7 ? `ABC-${i}` : `ROW-${i}` }))
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'code', header: 'Code' }]
const views: ViewsClient = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }

describe('ListPage paging and filters', () => {
  it('a search shows its own count and pages, from page 1', async () => {
    const resource = {
      basePath: '/things',
      list: vi.fn(async (spec: { page: number; pageSize: number; search?: string }) => {
        const hit = all.filter((r) => !spec.search || r.code.includes(spec.search))
        return { items: hit.slice((spec.page - 1) * spec.pageSize, spec.page * spec.pageSize), total: hit.length }
      }),
      meta: async () => ({ version: 1, searchable: true, default_sort: [], fields: [], views: [] }),
    }
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
      <ListPage<Row> resource={resource} columns={columns} title="Things" description="d" />
    </ViewsProvider></QueryClientProvider>)
    expect(await screen.findByText('1–50 of 120')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('Next page'))
    expect(await screen.findByText('51–100 of 120')).toBeInTheDocument()
    const box = screen.getByLabelText('Search')
    fireEvent.change(box, { target: { value: 'ABC' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(await screen.findByText('1–7 of 7')).toBeInTheDocument()
  })

  it('choosing how many per page shows that many, from page 1', async () => {
    const resource = {
      basePath: '/things',
      list: vi.fn(async (spec: { page: number; pageSize: number }) => ({
        items: all.slice((spec.page - 1) * spec.pageSize, spec.page * spec.pageSize), total: all.length })),
      meta: async () => ({ version: 1, searchable: true, default_sort: [], fields: [], views: [] }),
    }
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
      <ListPage<Row> resource={resource} columns={columns} title="Things" description="d" />
    </ViewsProvider></QueryClientProvider>)
    expect(await screen.findByText('1–50 of 120')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('Next page'))
    expect(await screen.findByText('51–100 of 120')).toBeInTheDocument()
    fireEvent.change(screen.getByDisplayValue('50'), { target: { value: '20' } })
    expect(await screen.findByText('1–20 of 120')).toBeInTheDocument()
  })
})
