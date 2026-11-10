import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { LocalTable } from './LocalTable'
import { EditableTable } from './EditableTable'
import { numberColumn, readColumn } from './editableColumns'
import type { ColumnDef } from './DataTable'

type Row = { id: number; item: string; qty: number }
const rows: Row[] = [{ id: 1, item: 'CF0092', qty: 12 }, { id: 2, item: 'CF0010', qty: 2 }, { id: 3, item: 'CF0093', qty: 30 }]
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: 'item', header: 'Item' }, { accessorKey: 'qty', header: 'EA' }]
const body = (t: HTMLElement) => within(t).getAllByRole('row').slice(1).map((r) => r.textContent)

describe('LocalTable', () => {
  it('sorts in the browser by a sortable column, both ways, and shows the footer', () => {
    render(<LocalTable<Row> ariaLabel="Lines" columns={columns} rows={rows} rowKey={(r) => r.id} sortable={['qty']}
      footer={['Total', '44']} />)
    const t = screen.getByRole('table', { name: 'Lines' })
    expect(body(t)).toEqual(['CF009212', 'CF00102', 'CF009330', 'Total44'])  // received order; the footer last
    fireEvent.click(within(t).getByRole('button', { name: /EA/ }))
    expect(body(t).slice(0, 3)).toEqual(['CF00102', 'CF009212', 'CF009330'])
    expect(within(t).getByRole('columnheader', { name: /EA/ })).toHaveAttribute('aria-sort', 'ascending')
    fireEvent.click(within(t).getByRole('button', { name: /EA/ }))
    expect(body(t).slice(0, 3)).toEqual(['CF009330', 'CF009212', 'CF00102'])
    expect(within(t).queryByRole('button', { name: /Item/ })).toBeNull()  // not sortable: no button
  })

  it('expands a row to its detail; a row with nothing to show has no ▸', () => {
    render(<LocalTable<Row> ariaLabel="Lines" columns={columns} rows={rows} rowKey={(r) => r.id}
      expand={(r) => (r.qty > 10 ? <p>lots of {r.item}</p> : null)} />)
    const t = screen.getByRole('table', { name: 'Lines' })
    const opens = within(t).getAllByRole('button', { name: 'Show details' })
    expect(opens).toHaveLength(2)
    fireEvent.click(opens[0])
    expect(within(t).getByText('lots of CF0092')).toBeInTheDocument()
    fireEvent.click(within(t).getByRole('button', { name: 'Hide details' }))
    expect(within(t).queryByText('lots of CF0092')).toBeNull()
  })

  it('starts with some columns hidden; the eye shows them, and the footer follows the columns', async () => {
    render(<LocalTable<Row> ariaLabel="Lines" columns={columns} rows={rows} rowKey={(r) => r.id} defaultHidden={['item']}
      footer={['Total', '44']} />)
    const t = screen.getByRole('table', { name: 'Lines' })
    expect(within(t).queryByText('CF0092')).toBeNull()
    expect(within(t).queryByText('Total')).toBeNull()  // its footer cell is hidden with it
    expect(t.querySelector('tfoot')).toHaveTextContent('44')
    fireEvent.click(within(t).getByRole('button', { name: 'Show, hide or move columns' }))
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Item' }))
    expect(within(t).getByText('CF0092')).toBeInTheDocument()
    expect(t.querySelector('tfoot')).toHaveTextContent('Total44')
  })

  it('an empty table says so and has no footer', () => {
    render(<LocalTable<Row> ariaLabel="Lines" columns={columns} rows={[]} rowKey={(r) => r.id} footer={['Total', '0']} emptyTitle="No lines" />)
    expect(screen.getByText('No lines')).toBeInTheDocument()
    expect(screen.queryByText('Total')).toBeNull()
  })
})

function Editor({ onRows }: { onRows?: (r: Row[]) => void }) {
  const [list, setList] = useState<Row[]>(rows.slice(0, 2))
  return (
    <EditableTable<Row> ariaLabel="Edit" rows={list} rowKey={(r) => r.id}
      onChange={(r) => { setList(r); onRows?.(r) }}
      columns={[readColumn<Row>('item', 'Item', (r) => r.item),
        numberColumn<Row>('qty', 'EA', { label: (r) => `EA ${r.item}`, get: (r) => r.qty, set: (v) => ({ qty: Number(v) }) })]}
      errors={(r) => (r.qty < 0 ? 'must be 0 or more' : null)}
      onAdd={() => ({ id: 9, item: 'NEW', qty: 0 })} removable />
  )
}

describe('EditableTable', () => {
  it('edits a cell, shows the row error and marks the input invalid', () => {
    let last: Row[] = []
    render(<Editor onRows={(r) => { last = r }} />)
    const input = screen.getByLabelText('EA CF0092')
    fireEvent.change(input, { target: { value: '-3' } })
    expect(last[0].qty).toBe(-3)
    expect(screen.getByRole('alert')).toHaveTextContent('must be 0 or more')
    expect(screen.getByLabelText('EA CF0092')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('EA CF0092').closest('tr')).toHaveAttribute('data-invalid', 'true')
    fireEvent.change(screen.getByLabelText('EA CF0092'), { target: { value: '6' } })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('adds and removes rows, never below the minimum', () => {
    render(<Editor />)
    fireEvent.click(screen.getByRole('button', { name: 'Add line' }))
    expect(screen.getByLabelText('EA NEW')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove line 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove line 1' }))
    expect(screen.getByRole('button', { name: 'Remove line 1' })).toBeDisabled()  // one row left
    expect(screen.getByLabelText('EA NEW')).toBeInTheDocument()
  })
})

describe('columns and rows (2026-11-10)', () => {
  it('a column can start hidden itself; the eye shows it', () => {
    const cols: ColumnDef<Row, unknown>[] = [{ accessorKey: 'item', header: 'Item' }, { accessorKey: 'qty', header: 'EA', meta: { hidden: true } }]
    render(<LocalTable<Row> ariaLabel="Lines" columns={cols} rows={rows} rowKey={(r) => r.id} />)
    const t = screen.getByRole('table', { name: 'Lines' })
    expect(within(t).queryByRole('columnheader', { name: /EA/ })).toBeNull()
    expect(body(t)).toEqual(['CF0092', 'CF0010', 'CF0093'])
  })

  it('a click on a cell opens the row, a click on a button or link in it never does', () => {
    const opened: number[] = []; const pressed: number[] = []
    const cols: ColumnDef<Row, unknown>[] = [
      { accessorKey: 'item', header: 'Item' },
      { id: 'act', header: 'Act', cell: ({ row }) => <button type="button" onClick={() => pressed.push(row.original.id)}>do {row.original.id}</button> },
      { id: 'link', header: 'Link', cell: () => <a href="#x">go</a> },
    ]
    render(<LocalTable<Row> ariaLabel="Lines" columns={cols} rows={rows} rowKey={(r) => r.id} onRowClick={(r) => opened.push(r.id)} />)
    fireEvent.click(screen.getByText('do 2'))
    fireEvent.click(screen.getAllByText('go')[0])
    expect(opened).toEqual([])
    expect(pressed).toEqual([2])
    fireEvent.click(screen.getByText('CF0093'))
    expect(opened).toEqual([3])
  })
})
