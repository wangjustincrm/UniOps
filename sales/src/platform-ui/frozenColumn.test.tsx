import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DataTable, type ColumnDef } from './primitives/DataTable'

// The first column stays put while the table scrolls sideways (user 2026-11-18): whatever is first now,
// with the leading cell (the chooser / ▸) frozen before it; the cards never.

type Row = { id: number; a: string; b: string }
const cols = (ids: string[]): ColumnDef<Row, unknown>[] => ids.map((id) => ({ accessorKey: id, header: id.toUpperCase() }))
const rows: Row[] = [{ id: 1, a: 'A1', b: 'B1' }]
const frozen = () => [...document.querySelectorAll('[data-frozen]')].map((el) => el.textContent)

describe('the frozen first column', () => {
  it('is the first column shown, after the leading cell', () => {
    const { rerender } = render(<DataTable<Row> ariaLabel="T" columns={cols(['a', 'b'])} rows={rows} rowKey={(r) => r.id} leadingHeader={<span>eye</span>} />)
    const th = document.querySelector('thead th[data-frozen]') as HTMLElement
    expect(th.textContent).toContain('A')
    expect(th.style.left).toBe('2rem')
    expect(th.className).toContain('sticky')
    expect(document.querySelector('tbody td:nth-child(2)')!.className).toContain('sticky')
    expect(frozen()).toEqual(['A'])
    rerender(<DataTable<Row> ariaLabel="T" columns={cols(['b', 'a'])} rows={rows} rowKey={(r) => r.id} />)  // reordered, no leading cell
    expect(frozen()).toEqual(['B'])
    expect((document.querySelector('thead th[data-frozen]') as HTMLElement).style.left).toBe('0px')
  })

  it('can be turned off', () => {
    render(<DataTable<Row> ariaLabel="T" columns={cols(['a', 'b'])} rows={rows} rowKey={(r) => r.id} freezeFirst={false} />)
    expect(frozen()).toEqual([])
  })
})
