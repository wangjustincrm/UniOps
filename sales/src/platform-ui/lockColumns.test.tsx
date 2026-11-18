import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { type ColumnDef, type QuerySpec } from './index'
import { QueryTable } from './patterns/QueryTable'
import { type FieldMeta, type ListMeta } from './resource/listMeta'
import { viewStateOf, type Layout, type ViewState } from './resource/viewState'

// Locked columns from a column's menu (user 2026-11-18): "Lock columns up to here" (at most 3, else refused with
// its reason), "Unlock columns"; saved in the view's layout; a 🔒 in the chooser; a page's own default.

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

describe('locking columns from the column menu', () => {
  it('locks up to a column, saves it in the view, shows 🔒 in the chooser, and unlocks', async () => {
    const t = await setup()
    expect(frozen()).toEqual(['a'])                                   // the default: 1
    fireEvent.click(within(await menu('B')).getByText('Lock columns up to here'))
    expect(frozen()).toEqual(['a', 'b'])
    expect(t.last().locked).toBe(2)
    expect(viewStateOf({ page: 1, pageSize: 50 }, { columns: [], hidden: [], locked: 2 }).locked).toBe(2)
    fireEvent.click(within(await menu('A')).getByText('Unlock columns'))
    expect(frozen()).toEqual([])
    expect(t.last().locked).toBe(0)
    fireEvent.click(within(await menu('C')).getByText('Lock columns up to here'))
    fireEvent.click(screen.getByRole('button', { name: 'Show, hide or move columns' }))
    expect(within(screen.getByRole('list', { name: 'On this list' })).getAllByLabelText('locked')).toHaveLength(3)
    expect(document.querySelectorAll('thead [aria-label="locked"]')).toHaveLength(3)  // and on the headers
  })

  it('a fourth is refused with its reason; a page sets its own default', async () => {
    await setup(2)
    expect(frozen()).toEqual(['a', 'b'])
    const m = await menu('D')
    const item = within(m).getByText('Lock columns up to here').closest('[role="menuitem"]')!
    expect(item).toHaveAttribute('data-disabled')
    expect(within(m).getByText(/At most 3 locked columns/)).toBeInTheDocument()
  })
})
