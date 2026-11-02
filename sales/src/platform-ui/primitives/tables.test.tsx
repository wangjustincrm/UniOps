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
