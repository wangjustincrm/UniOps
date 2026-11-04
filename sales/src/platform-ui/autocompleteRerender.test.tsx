import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Autocomplete, LocalTable, type ColumnDef, type EditableColumn } from './index'
import { EditableTable } from './primitives/EditableTable'

// An open Autocomplete survives its parent re-rendering (a list refetch landing
// under an open form, 2026-11-03). The cause was in the tables: a cell given as
// a function was rendered as a component type, and pages rebuild their columns
// on every render — so each re-render was a new type, the cell remounted and
// the popover closed. Cells and headers now render through one stable component.

const options = [{ value: 'CF0092', label: 'CF0092', hint: 'Formula stage 1' }, { value: 'CF0010', label: 'CF0010' }]
const load = vi.fn(async (q: string) => options.filter((o) => o.value.includes(q.toUpperCase())))

type Line = { item: string | null }
// The parent re-renders on demand (a button — a click event, no pointer-down, so the popover
// sees no outside press; in the real page it is the list's refetch landing).
const rerender = () => fireEvent.click(screen.getByRole('button', { name: 're-render' }))

function Form() {
  const [, setTick] = useState(0)
  const [lines, setLines] = useState<Line[]>([{ item: null }])
  // Built inline on every render, as the pages do (MasterDetailForm, ManualOrderForm).
  const columns: EditableColumn<Line>[] = [{
    id: 'item', header: 'Item', cell: ({ row, update }) => (
      <Autocomplete aria-label="Item 1" value={row.item} loadOptions={load} debounceMs={0} onChange={(v) => update({ item: v })} />
    ),
  }]
  return <>
    <button type="button" onClick={() => setTick((n) => n + 1)}>re-render</button>
    <EditableTable<Line> ariaLabel="Lines" rows={lines} onChange={setLines} columns={columns} />
  </>
}

describe('an open Autocomplete and a parent re-render', () => {
  it('in a table whose columns are rebuilt each render: the listbox stays open while it loads and after', async () => {
    render(<Form />)
    const trigger = screen.getByRole('combobox', { name: 'Item 1' })
    fireEvent.click(trigger)
    const listId = trigger.getAttribute('aria-controls')!
    await waitFor(() => expect(document.getElementById(listId)).not.toBeNull())
    act(() => rerender())  // e.g. the list's first page lands while the listbox is opening
    act(() => rerender())
    expect(document.getElementById(listId)).not.toBeNull()
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const listbox = document.getElementById(listId)!
    expect(await within(listbox).findByRole('option', { name: /CF0092/ })).toBeInTheDocument()
    act(() => rerender())  // and once loaded
    fireEvent.click(within(document.getElementById(listId)!).getByRole('option', { name: /CF0092/ }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Item 1' })).toHaveTextContent('CF0092 — Formula stage 1'))
  })

  it('a cell with its own state keeps it across re-renders (no remount); a header the same', () => {
    const mounted = vi.fn()
    function Counter() {
      const [n, setN] = useState(0)
      useState(() => mounted())
      return <button type="button" onClick={() => setN(n + 1)}>clicked {n}</button>
    }
    function Page() {
      const [, setTick] = useState(0)
      const columns: ColumnDef<{ id: number }, unknown>[] = [{ id: 'c', header: () => <span>Head</span>, cell: () => <Counter /> }]
      return <>
        <button type="button" onClick={() => setTick((x) => x + 1)}>re-render</button>
        <LocalTable ariaLabel="T" columns={columns} rows={[{ id: 1 }]} rowKey={(r) => r.id} />
      </>
    }
    render(<Page />)
    fireEvent.click(screen.getByRole('button', { name: 'clicked 0' }))
    act(() => rerender())
    act(() => rerender())
    expect(screen.getByRole('button', { name: 'clicked 1' })).toBeInTheDocument()
    expect(mounted).toHaveBeenCalledOnce()
    expect(screen.getByText('Head')).toBeInTheDocument()
  })
})
