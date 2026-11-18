import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DataTable, type ColumnDef } from './primitives/DataTable'

// Locked columns (user 2026-11-18): the first N shown columns stay put while the table scrolls sideways — each
// after the leading cell (the chooser / ▸) and the measured widths of the locked ones before it; 0 none; the cards never.

type Row = { id: number; a: string; b: string; c: string }
const cols = (ids: string[]): ColumnDef<Row, unknown>[] => ids.map((id) => ({ accessorKey: id, header: id.toUpperCase() }))
const rows: Row[] = [{ id: 1, a: 'A1', b: 'B1', c: 'C1' }]
const frozen = () => [...document.querySelectorAll('thead th[data-frozen]')].map((el) => el.textContent)
const leftOf = (id: string) => (document.querySelector(`thead th[data-col="${id}"]`) as HTMLElement).style.left
// jsdom lays nothing out: each header is as wide as its column says
const WIDTHS: Record<string, number> = { a: 100, b: 140, c: 80 }
afterEach(() => vi.restoreAllMocks())
const measure = () => vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
  return { width: WIDTHS[this.getAttribute('data-col') ?? ''] ?? 0 } as DOMRect
})

describe('locked columns', () => {
  it('the first shown column by default, after the leading cell, whatever is first now', () => {
    const { rerender } = render(<DataTable<Row> ariaLabel="T" columns={cols(['a', 'b'])} rows={rows} rowKey={(r: Row) => r.id} leadingHeader={<span>eye</span>} />)
    expect(frozen()).toEqual(['A'])
    expect(leftOf('a')).toBe('32px')
    expect(document.querySelector('tbody td:nth-child(2)')!.className).toContain('sticky')
    rerender(<DataTable<Row> ariaLabel="T" columns={cols(['b', 'a'])} rows={rows} rowKey={(r: Row) => r.id} />)  // reordered, no leading cell
    expect(frozen()).toEqual(['B'])
    expect(leftOf('b')).toBe('0px')
  })

  it('several: each after the measured widths of those before it; 0 locks none', () => {
    measure()
    const { rerender } = render(<DataTable<Row> ariaLabel="T" columns={cols(['a', 'b', 'c'])} rows={rows} rowKey={(r: Row) => r.id} locked={3} leadingHeader={<span>eye</span>} />)
    expect(frozen()).toEqual(['A', 'B', 'C'])
    expect([leftOf('a'), leftOf('b'), leftOf('c')]).toEqual(['32px', '132px', '272px'])
    expect((document.querySelector('thead th[data-col="c"]') as HTMLElement).className).toContain('shadow')  // the edge on the last one only
    expect((document.querySelector('thead th[data-col="a"]') as HTMLElement).className).not.toContain('shadow')
    rerender(<DataTable<Row> ariaLabel="T" columns={cols(['a', 'b', 'c'])} rows={rows} rowKey={(r: Row) => r.id} locked={0} />)
    expect(frozen()).toEqual([])
  })
})
