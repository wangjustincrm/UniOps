import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { type ColumnDef, type QuerySpec } from './index'
import { QueryTable } from './patterns/QueryTable'
import { type FieldMeta, type ListMeta } from './resource/listMeta'
import { type Layout, type ViewState } from './resource/viewState'

// Locked columns (user 2026-11-18): "Lock column" joins the end of the locked group, "Unlock column" leaves to the first
// place after it (the others stay frozen, in order); at most 3, else refused with its reason; saved in the view's layout
// (order + count); the same toggle in the chooser; no lock icon in the headers; a page's own default.

const f = (name: string, label: string): FieldMeta => ({
  name, label, type: 'text', enum_values: [], filterable: true, sortable: true, groupable: false, operators: ['eq'], in_row: true })
const meta: ListMeta = { version: 1, searchable: false, default_sort: [], fields: ['a', 'b', 'c', 'd'].map((k) => f(k, k.toUpperCase())) }
type Row = { id: number; a: string; b: string; c: string; d: string }
const rows: Row[] = [{ id: 1, a: 'A1', b: 'B1', c: 'C1', d: 'D1' }]
const columns: ColumnDef<Row, unknown>[] = ['a', 'b', 'c', 'd'].map((k) => ({ accessorKey: k, header: k.toUpperCase() }))

function Harness({ onView, defaultLocked }: { onView: (v: ViewState) => void; defaultLocked?: number }) {
  const [spec, setSpec] = useState<QuerySpec>({ page: 1, pageSize: 50 })
  const [layout, setLayout] = useState<Layout>({ columns: [], hidden: [] })
  return <QueryTable<Row> ariaLabel="Things" columns={columns} rows={rows} total={1} spec={spec} onSpecChange={setSpec} layout={layout}
    onLayoutChange={setLayout} onViewChange={onView} defaultLocked={defaultLocked}
    resource={{ basePath: '/things', meta: async () => meta }} rowKey={(r) => r.id} />
}
const setup = async (defaultLocked?: number) => {
  const onView = vi.fn()
  render(<QueryClientProvider client={new QueryClient()}><Harness onView={onView} defaultLocked={defaultLocked} /></QueryClientProvider>)
  await screen.findByRole('button', { name: 'D column menu' })
  return { last: (): ViewState => onView.mock.calls.at(-1)![0] }
}
const menu = async (col: string) => {
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())  // the last one closed
  fireEvent.keyDown(screen.getByRole('button', { name: `${col} column menu` }), { key: 'Enter' })
  return screen.findByRole('menu')
}
const frozen = () => [...document.querySelectorAll('thead th[data-frozen]')].map((el) => el.getAttribute('data-col'))

const order = () => [...document.querySelectorAll('thead th[data-col]')].map((el) => el.getAttribute('data-col'))
const pick = async (col: string, item: string) => fireEvent.click(within(await menu(col)).getByText(item))

describe('locking columns (user 2026-11-18): Lock column / Unlock column', () => {
  it('a locked column joins the end of the locked group; the view saves order and count', async () => {
    const t = await setup()
    expect(frozen()).toEqual(['a'])                                   // the default: 1
    await pick('C', 'Lock column')
    expect(order()).toEqual(['a', 'c', 'b', 'd'])
    expect(frozen()).toEqual(['a', 'c'])
    expect(t.last().locked).toBe(2)
    expect(t.last().columns.slice(0, 2)).toEqual(['a', 'c'])
  })

  it('unlocking one in the middle keeps the others frozen and in order; it goes right after the group', async () => {
    const t = await setup()
    await pick('C', 'Lock column')
    await pick('D', 'Lock column')
    expect(order()).toEqual(['a', 'c', 'd', 'b'])
    await pick('C', 'Unlock column')
    expect(order()).toEqual(['a', 'd', 'c', 'b'])
    expect(frozen()).toEqual(['a', 'd'])
    expect(t.last().locked).toBe(2)
  })

  it('a fourth is refused with its reason; the headers show no lock icon', async () => {
    await setup(3)
    expect(frozen()).toEqual(['a', 'b', 'c'])
    const m = await menu('D')
    const item = within(m).getByText('Lock column').closest('[role="menuitem"]')!
    expect(item).toHaveAttribute('data-disabled')
    expect(within(m).getByText(/At most 3 locked columns: unlock one first/)).toBeInTheDocument()
    expect(document.querySelector('thead .lucide-lock, thead [aria-label="locked"]')).toBeNull()
  })

  it('the chooser locks and unlocks a shown column the same way', async () => {
    await setup()
    fireEvent.click(screen.getByRole('button', { name: 'Show, hide or move columns' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Lock D' }))
    expect(order()).toEqual(['a', 'd', 'b', 'c'])
    fireEvent.click(screen.getByRole('button', { name: 'Unlock A' }))
    expect(frozen()).toEqual(['d'])
    expect(order()).toEqual(['d', 'a', 'b', 'c'])
  })
})
