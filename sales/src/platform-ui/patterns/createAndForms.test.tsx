import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { z } from 'zod'
import { ListPage } from './ListPage'
import { MasterDetailForm } from './MasterDetailForm'
import { PermissionProvider } from './PermissionGate'
import { ViewsProvider } from './ViewsProvider'
import { numberColumn, textColumn } from '../primitives/editableColumns'
import type { ListMeta } from '../resource/listMeta'
import type { ViewsClient } from '../resource/viewsApi'

type Rec = { id: number; name: string }
const meta: ListMeta = { version: 1, searchable: false, default_sort: [], fields: [], views: [{ key: 'all', name: 'All', state: { version: 1 } }] }
const views: ViewsClient = { list: vi.fn(async () => []), create: vi.fn(), update: vi.fn(), remove: vi.fn(), setDefault: vi.fn() }

function wrap(ui: React.ReactNode, keys: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ViewsProvider client={views}>
    <PermissionProvider can={(k) => keys.includes(k)}>{ui}</PermissionProvider></ViewsProvider></QueryClientProvider>)
}

describe('ListPage create', () => {
  it('New is hidden without the permission; with it the form opens in the sheet, and saving refreshes and opens the record', async () => {
    const rows: Rec[] = [{ id: 1, name: 'first' }]
    const resource = { basePath: '/things', list: vi.fn(async () => ({ items: [...rows], total: rows.length })), meta: async () => meta }
    const page = (
      <ListPage<Rec> resource={resource} title="Things" columns={[{ accessorKey: 'name', header: 'Name' }]}
        detailTitle={(r) => `Thing ${r.name}`} renderDetail={(r) => <p>detail of {r.name}</p>}
        create={{ label: 'New thing', permission: 'things.make',
          render: (done, cancel) => (
            <div>
              <button type="button" onClick={cancel}>Cancel</button>
              <button type="button" onClick={() => { rows.push({ id: 2, name: 'second' }); done({ id: 2, name: 'second' }) }}>Make it</button>
            </div>
          ) }} />
    )
    const { unmount } = wrap(page, [])
    await screen.findByText('first')
    expect(screen.queryByRole('button', { name: 'New thing' })).toBeNull()
    unmount()
    wrap(page, ['things.make'])
    fireEvent.click(await screen.findByRole('button', { name: 'New thing' }))
    const sheet = await screen.findByRole('dialog', { name: 'New thing' })
    const calls = resource.list.mock.calls.length
    fireEvent.click(within(sheet).getByRole('button', { name: 'Make it' }))
    expect(await screen.findByRole('dialog', { name: 'Thing second' })).toHaveTextContent('detail of second')
    await waitFor(() => expect(resource.list.mock.calls.length).toBeGreaterThan(calls))  // refreshed
    expect(screen.queryByRole('dialog', { name: 'New thing' })).toBeNull()
  })
})

type Line = { item: string; qty: string }

function Form({ onSubmit }: { onSubmit: (v: Record<string, unknown>, lines: Line[]) => Promise<unknown> }) {
  const [blocked] = useState<string | null>(null)
  return (
    <MasterDetailForm<Line>
      schema={z.object({ name: z.string().min(1, 'Required') })}
      fields={[{ name: 'name', label: 'Name', required: true }]}
      defaultValues={{ name: '' }}
      lines={{ label: 'Lines', initial: [{ item: '', qty: '' }], add: () => ({ item: '', qty: '' }), removable: true,
        columns: [textColumn<Line>('item', 'Item', { label: (_, i) => `Item ${i + 1}`, get: (l) => l.item, set: (v) => ({ item: v }) }),
          numberColumn<Line>('qty', 'Qty', { label: (_, i) => `Qty ${i + 1}`, get: (l) => l.qty, set: (v) => ({ qty: v }) })],
        problem: (l) => (!l.item ? 'an item' : Number(l.qty) > 0 ? null : 'a quantity') }}
      blocked={() => blocked}
      onSubmit={onSubmit}
    />
  )
}

describe('MasterDetailForm', () => {
  it('shows line errors and holds the submit; header errors on submit; submits once', async () => {
    let release: () => void = () => {}
    const onSubmit = vi.fn(() => new Promise<void>((r) => { release = r }))
    render(<Form onSubmit={onSubmit} />)
    const save = screen.getByRole('button', { name: 'Save' })
    expect(screen.getByRole('alert')).toHaveTextContent('an item')
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Item 1'), { target: { value: 'CF0092' } })
    fireEvent.change(screen.getByLabelText('Qty 1'), { target: { value: '6' } })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    expect(await screen.findByText('Required')).toBeInTheDocument()  // the header field, next to it
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Mine' } })
    fireEvent.click(save)
    fireEvent.click(save)  // a second click while the first is pending
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit).toHaveBeenCalledWith({ name: 'Mine' }, [{ item: 'CF0092', qty: '6' }])
    release()
  })

  it('a server refusal naming a field goes next to it', async () => {
    const err = Object.assign(new Error('taken'), { body: { error: { code: 'x.taken', message: 'That name is taken', details: { field: 'name' } } } })
    render(<Form onSubmit={async () => { throw err }} />)
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Mine' } })
    fireEvent.change(screen.getByLabelText('Item 1'), { target: { value: 'A' } })
    fireEvent.change(screen.getByLabelText('Qty 1'), { target: { value: '1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('That name is taken')).toBeInTheDocument()
  })
})

describe('Autocomplete', () => {
  it('opened with nothing typed it shows the first page; the chosen value shows label and hint', async () => {
    const { Autocomplete } = await import('../primitives/Autocomplete')
    const load = vi.fn(async (q: string) => [{ value: 'CF0092', label: 'CF0092', hint: 'Formula stage 1' }, { value: 'CF0010', label: 'CF0010', hint: `q=${q}` }])
    function Pick() {
      const [v, setV] = useState<string | null>(null)
      return <Autocomplete aria-label="Item" value={v} onChange={setV} loadOptions={load} debounceMs={0} />
    }
    render(<Pick />)
    fireEvent.click(screen.getByRole('combobox', { name: 'Item' }))
    expect(await screen.findByRole('option', { name: /CF0092/ })).toBeInTheDocument()  // nothing typed
    expect(load).toHaveBeenCalledWith('')
    expect(screen.getByText('Type to search…')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /CF0092/ }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Item' })).toHaveTextContent('CF0092 — Formula stage 1'))
  })

  it('a value set from outside is shown with its label and hint too', async () => {
    const { Autocomplete } = await import('../primitives/Autocomplete')
    const load = vi.fn(async (q: string) => (q === '7' ? [] : [{ value: '7', label: 'KGN', hint: 'Kingston' }]))
    render(<Autocomplete aria-label="From" value="7" onChange={() => {}} loadOptions={load} />)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'From' })).toHaveTextContent('KGN — Kingston'))  // found in the first page
  })
})
