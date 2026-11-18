import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { type ColumnDef, type QuerySpec } from './index'
import { QueryTable } from './patterns/QueryTable'
import { type FieldMeta, type ListMeta } from './resource/listMeta'
import type { Layout } from './resource/viewState'

// Grouping by drag and drop (user 2026-11-18): a header's grip dropped on the Grouping row groups by it, with the
// column menu's rules (groupable, MAX_GROUP_KEYS) and the same spec; a drop on another header still only moves
// the column; a grouping chip is dragged to another level, or out of the row to stop grouping.

const f = (name: string, label: string, groupable: boolean): FieldMeta => ({
  name, label, type: 'text', enum_values: [], filterable: true, sortable: true, groupable, operators: ['eq'], in_row: true })
const meta: ListMeta = { version: 1, searchable: false, default_sort: [], fields: [
  f('code', 'Item code', true), f('price', 'Price', false), f('status', 'Status', true), f('kind', 'Kind', true), f('lot', 'Lot', true)] }
type Row = { id: number; code: string; price: string; status: string; kind: string; lot: string }
const rows: Row[] = [{ id: 1, code: 'A1', price: '9.50', status: 'Open', kind: 'X', lot: 'L1' }]
const columns: ColumnDef<Row, unknown>[] = ['code', 'price', 'status', 'kind', 'lot'].map((k) => ({ accessorKey: k, header: meta.fields.find((x) => x.name === k)!.label }))

function Harness({ initial, onSpec, onLayout }: { initial?: QuerySpec; onSpec: (s: QuerySpec) => void; onLayout: (l: Layout) => void }) {
  const [spec, setSpec] = useState<QuerySpec>(initial ?? { page: 2, pageSize: 50 })
  const [layout, setLayout] = useState<Layout>({ columns: [], hidden: [] })
  return <QueryTable<Row> ariaLabel="Things" columns={columns} rows={rows} total={1} spec={spec} layout={layout}
    onLayoutChange={(l) => { setLayout(l); onLayout(l) }} onSpecChange={(s) => { setSpec(s); onSpec(s) }}
    resource={{ basePath: '/things', meta: async () => meta }} rowKey={(r) => r.id} />
}
const setup = async (initial?: QuerySpec) => {
  const onSpec = vi.fn(); const onLayout = vi.fn()
  render(<QueryClientProvider client={new QueryClient()}><Harness initial={initial} onSpec={onSpec} onLayout={onLayout} /></QueryClientProvider>)
  await screen.findByRole('button', { name: 'Item code column menu' })
  return { onSpec, onLayout, last: (): QuerySpec => onSpec.mock.calls.at(-1)![0] }
}
/** A DataTransfer for jsdom: what one drag carries from its dragstart to its drop. */
const transfer = () => {
  const data = new Map<string, string>()
  return { data, dropEffect: 'none', effectAllowed: 'all', get types() { return [...data.keys()] },
    setData: (t: string, v: string) => { data.set(t, v) }, getData: (t: string) => data.get(t) ?? '' }
}
const grip = (id: string) => document.querySelector(`[data-column-grip="${id}"]`)!
const row = () => document.querySelector('[data-group-drop]') as HTMLElement | null
const dragHeaderTo = (id: string, target: () => Element) => {
  const dt = transfer()
  fireEvent.dragStart(grip(id), { dataTransfer: dt })
  const el = target()
  fireEvent.dragEnter(el, { dataTransfer: dt })
  fireEvent.dragOver(el, { dataTransfer: dt })
  fireEvent.drop(el, { dataTransfer: dt })
  fireEvent.dragEnd(grip(id), { dataTransfer: dt })
  return dt
}

describe('grouping by drag and drop', () => {
  it('the Grouping row appears only while a header is dragged, and says what a drop does', async () => {
    await setup()
    expect(row()).toBeNull()
    fireEvent.dragStart(grip('status'), { dataTransfer: transfer() })
    expect(row()).toHaveAttribute('data-group-drop', 'open')
    expect(screen.getByText('Drop here to group by Status')).toBeInTheDocument()
    fireEvent.dragEnd(grip('status'), { dataTransfer: transfer() })
    expect(row()).toBeNull()
  })

  it('a drop on the row groups by the column — the same spec as the menu, page 1, and no column moved', async () => {
    const t = await setup()
    dragHeaderTo('status', () => row()!)
    const dropped = t.last()
    expect(dropped.group).toEqual([{ field: 'status', desc: false }])
    expect(dropped.page).toBe(1)
    expect(t.onLayout).not.toHaveBeenCalled()
    cleanup()
    const m = await setup()
    fireEvent.keyDown(screen.getByRole('button', { name: 'Status column menu' }), { key: 'Enter' })
    fireEvent.click(within(await screen.findByRole('menu')).getByText('Group ascending'))
    expect(m.last()).toEqual(dropped)
  })

  it('a drop on another header only moves the column', async () => {
    const t = await setup()
    dragHeaderTo('status', () => grip('code').closest('th')!)
    expect(t.onSpec).not.toHaveBeenCalled()
    expect(t.onLayout.mock.calls.at(-1)![0].columns.slice(0, 2)).toEqual(['status', 'code'])
  })

  it('refuses, with its reason, a column that cannot be grouped and a fourth level', async () => {
    const t = await setup()
    fireEvent.dragStart(grip('price'), { dataTransfer: transfer() })
    expect(row()).toHaveAttribute('data-group-drop', 'refused')
    expect(screen.getByText('Price cannot be grouped')).toBeInTheDocument()
    fireEvent.drop(row()!, { dataTransfer: transfer() })
    expect(t.onSpec).not.toHaveBeenCalled()
    fireEvent.dragEnd(grip('price'), { dataTransfer: transfer() })
    cleanup()
    const full = await setup({ page: 1, pageSize: 50, group: [{ field: 'code' }, { field: 'status' }, { field: 'kind' }] })
    fireEvent.dragStart(grip('lot'), { dataTransfer: transfer() })
    expect(screen.getByText('At most 3 groupings: remove one first')).toBeInTheDocument()
    fireEvent.drop(row()!, { dataTransfer: transfer() })
    expect(full.onSpec).not.toHaveBeenCalled()
  })

  it('a grouping chip dragged onto another changes the nesting; dragged out of the row, it is removed', async () => {
    const t = await setup({ page: 1, pageSize: 50, group: [{ field: 'code' }, { field: 'status' }] })
    const chip = (id: string) => document.querySelector(`[data-group-chip="${id}"]`)!
    const dt = transfer()
    fireEvent.dragStart(chip('status'), { dataTransfer: dt })
    fireEvent.dragOver(chip('code'), { dataTransfer: dt })
    fireEvent.drop(chip('code'), { dataTransfer: dt })
    dt.dropEffect = 'move'
    fireEvent.dragEnd(chip('status'), { dataTransfer: dt })
    expect(t.last().group?.map((g) => g.field)).toEqual(['status', 'code'])
    const out = transfer()                                 // dropped nowhere: dropEffect stays 'none'
    fireEvent.dragStart(chip('code'), { dataTransfer: out })
    fireEvent.dragEnd(chip('code'), { dataTransfer: out })
    expect(t.last().group?.map((g) => g.field)).toEqual(['status'])
  })
})
