import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { type ColumnDef, type QuerySpec } from './index'
import type { ListTotal } from './resource/querySpec'
import { QueryTable } from './patterns/QueryTable'
import { type FieldMeta, type ListMeta } from './resource/listMeta'

// User 2026-11-18: any field of a list's rows can be added as a column from the chooser — the page's own
// columns under "On this list", every other field the rows carry under "More fields", a find box, and
// tri-state boxes (all / none / a dash for some) for the whole list and each group.

const f = (name: string, label: string, type: FieldMeta['type'], extra: Partial<FieldMeta> = {}): FieldMeta => ({
  name, label, type, enum_values: [], filterable: true, sortable: true, groupable: false, operators: ['eq'], in_row: true, ...extra })
const meta: ListMeta = {
  version: 1, searchable: false, default_sort: [],
  fields: [
    f('ref', 'Code', 'text'), f('partner', 'Partner', 'text'), f('currency', 'Currency', 'text'),
    f('net', 'Net', 'number', { money: true, currency_field: 'currency' }), f('state', 'State', 'enum', { enum_values: ['Open'] }),
    f('weight', 'Weight', 'number'), f('tags', 'Tags', 'text'), f('channel', 'Channel', 'text'), f('paid', 'Paid', 'datetime'),
    f('refs', 'Codes', 'text', { in_row: false }),  // a filter-only key: never a column
  ],
}
type Row = { id: number; ref: string; partner: string; currency: string; net: string; state: string; weight: string }
const rows: Row[] = [{ id: 1, ref: 'A-1', partner: 'Acme', currency: 'CAD', net: '254.40', state: 'Open', weight: '12.50' }]
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'ref', header: 'Code' }, { accessorKey: 'partner', header: 'Partner' }]

const totals: ListTotal[] = [{ currency: 'CAD', count: 1, sums: { net: '254.40' } }]
function Harness() {
  const [spec, setSpec] = useState<QuerySpec>({ page: 1, pageSize: 50 })
  return <QueryTable<Row> ariaLabel="Things" columns={columns} rows={rows} total={1} spec={spec} onSpecChange={setSpec} totals={totals}
    resource={{ basePath: '/things', meta: async () => meta }} rowKey={(r) => r.id} />
}
const open = async () => {
  render(<QueryClientProvider client={new QueryClient()}><Harness /></QueryClientProvider>)
  const t = screen.getByRole('table', { name: 'Things' })
  fireEvent.click(await within(t).findByRole('button', { name: 'Show, hide or move columns' }))
  await screen.findByRole('group', { name: 'More fields' })
  return t
}
const box = (name: string) => screen.getByRole('checkbox', { name })
const header = (t: HTMLElement) => [...t.querySelectorAll('thead th')].map((th) => th.textContent ?? '')

describe('the column chooser: every field of the row', () => {
  it('lists the page columns, then each field the rows carry; never a filter-only key', async () => {
    await open()
    expect(within(screen.getByRole('list', { name: 'On this list' })).getAllByRole('checkbox')).toHaveLength(2)
    const more = within(screen.getByRole('group', { name: 'More fields' }))
    expect(more.getByText('Net')).toBeInTheDocument()
    expect(more.getByText('Weight')).toBeInTheDocument()
    expect(screen.queryByText('Codes')).toBeNull()
  })

  it('a field added shows as its kind: money with the row currency, an enum as a badge, a plain number without one', async () => {
    const t = await open()
    fireEvent.click(box('Net'))
    fireEvent.click(box('State'))
    fireEvent.click(box('Weight'))
    expect(within(t.querySelector('tbody')!).getByText(/254\.40/).textContent).toContain('CAD')
    expect(within(t).getByText('Open')).toBeInTheDocument()
    expect(within(t).getByText('12.5').textContent).not.toContain('CAD')
  })

  it('the boxes for all and for each group: a dash for some, all on, all off (the first column stays)', async () => {
    const t = await open()
    const all = box('Show or hide all columns')
    expect(all).toHaveAttribute('data-state', 'indeterminate')         // the page's 2 of 9 shown
    expect(box('Show or hide all of More fields')).toHaveAttribute('data-state', 'unchecked')
    fireEvent.click(box('Show or hide all of More fields'))
    expect(header(t).join('|')).toContain('Weight')
    expect(all).toHaveAttribute('data-state', 'checked')
    fireEvent.click(all)                                                // all off: one column always stays
    expect(header(t).filter((h) => /Code|Partner|Currency|Net|State|Weight|Tags|channel|Paid/.test(h))).toEqual(['Code'])
    expect(all).toHaveAttribute('data-state', 'indeterminate')
  })

  it('the find box narrows the list and the all box to the matches', async () => {
    const t = await open()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a field' }), { target: { value: 'ne' } })
    expect(screen.queryByText('Weight')).toBeNull()
    fireEvent.click(box('Show or hide all columns'))                     // only "Net" matches among the hidden
    expect(header(t).join('|')).toContain('Net')
    expect(header(t).join('|')).not.toContain('Weight')
  })

  it('an amount added as a column shows its total under it', async () => {
    const t = await open()
    expect(t.querySelector('tfoot')).toBeNull()                         // the amount hidden: no footer
    fireEvent.click(box('Net'))
    expect(t.querySelector('[data-total="net"]')?.textContent).toContain('254.40')
  })
})
