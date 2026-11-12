import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { CrudPage, PermissionProvider, ViewsProvider, type ColumnDef, type FieldSpec } from './index'
import type { ResourceClient } from './resource/ResourceClient'

// CrudPage's own behaviour, on a two-field record: rendering a real page's form
// three times over (open, re-key on the new version, save) made this test
// depend on the machine's load (2026-11-04), and it is not about that page.

type Rec = { id: number; name: string; terms: string | null; version: number }
const rec: Rec = { id: 1, name: 'A', terms: null, version: 2 }
const columns: ColumnDef<Rec, unknown>[] = [{ accessorKey: 'name', header: 'Name' }]
const fields: FieldSpec[] = [{ name: 'name', label: 'Name' }, { name: 'terms', label: 'Terms' }]
const schema = z.object({ name: z.string(), terms: z.string().nullable().optional() })
const views = { list: async () => [], create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }

describe('CrudPage keeps the open record current', () => {
  it('a save after the record changed underneath uses its current version', async () => {
    const list = vi.fn(async () => ({ items: [rec], total: 1 }))
    const update = vi.fn(async () => ({ ...rec, name: 'X', version: 4 }))
    const client = { basePath: '/crud', list, update, exportPath: () => '',
      meta: async () => ({ version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }),
    } as unknown as ResourceClient<Rec, Rec, Rec>
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ViewsProvider client={views}><PermissionProvider can={() => true}>
      <CrudPage<Rec, Rec, Rec> title="Things" client={client} columns={columns} writePermission="w" createSchema={schema} updateSchema={schema}
        fields={fields} emptyValues={{ name: '', terms: '' }} toFormValues={(r) => ({ ...r })} />
    </PermissionProvider></ViewsProvider></QueryClientProvider>)
    fireEvent.click(await screen.findByText('A'))
    const sheet = await screen.findByRole('dialog')
    list.mockResolvedValue({ items: [{ ...rec, version: 3, terms: 'Net 30' }], total: 1 })
    await qc.invalidateQueries()
    await waitFor(() => expect(within(sheet).getByLabelText('Terms')).toHaveValue('Net 30'))
    fireEvent.change(within(sheet).getByLabelText('Name'), { target: { value: 'X' } })
    fireEvent.click(within(sheet).getByText('Save'))
    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { name: 'X' }, 3))
  })

  it('a save keeps the record open, says Saved and shows what was saved (no page refresh)', async () => {
    let saved = rec
    const list = vi.fn(async () => ({ items: [saved], total: 1 }))
    const update = vi.fn(async (_id: number, data: Partial<Rec>) => { saved = { ...saved, ...data, version: saved.version + 1 }; return saved })
    const client = { basePath: '/crud2', list, update, exportPath: () => '',
      meta: async () => ({ version: 1, searchable: true, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }),
    } as unknown as ResourceClient<Rec, Rec, Rec>
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><ViewsProvider client={views}><PermissionProvider can={() => true}>
      <CrudPage<Rec, Rec, Rec> title="Things" client={client} columns={columns} writePermission="w" createSchema={schema} updateSchema={schema}
        fields={fields} emptyValues={{ name: '', terms: '' }} toFormValues={(r) => ({ ...r })} />
    </PermissionProvider></ViewsProvider></QueryClientProvider>)
    fireEvent.click(await screen.findByText('A'))
    const sheet = await screen.findByRole('dialog')
    fireEvent.change(within(sheet).getByLabelText('Terms'), { target: { value: 'Net 45' } })
    fireEvent.click(within(sheet).getByText('Save'))
    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { terms: 'Net 45' }, 2))
    expect(await within(sheet).findByText('Saved')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBe(sheet)                                           // still open
    await waitFor(() => expect(within(sheet).getByLabelText('Terms')).toHaveValue('Net 45'))  // the saved value
    await waitFor(() => expect(screen.getAllByText('A').length).toBeGreaterThan(0))
  })
})
