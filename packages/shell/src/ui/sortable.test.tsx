// @vitest-environment node
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { SortTh, nextSort, sortParams, sortRows, type Sort } from './sortable'

interface Row { id: number; no: string | null; amount: number | null; date?: string | null; name?: string }
type K = 'no' | 'amount' | 'date' | 'name'
const getters = {
  no: (r: Row) => r.no,
  amount: (r: Row) => r.amount,
  date: (r: Row) => r.date,
  name: (r: Row) => r.name,
}
const ids = (rows: Row[]) => rows.map((r) => r.id)

describe('nextSort', () => {
  it('starts ascending, then reverses, then flips back', () => {
    const a = nextSort<K>(null, 'no')
    expect(a).toEqual({ key: 'no', dir: 'asc' })
    const b = nextSort(a, 'no')
    expect(b).toEqual({ key: 'no', dir: 'desc' })
    expect(nextSort(b, 'no')).toEqual({ key: 'no', dir: 'asc' })
  })
  it('another column starts ascending', () => {
    expect(nextSort<K>({ key: 'no', dir: 'desc' }, 'amount')).toEqual({ key: 'amount', dir: 'asc' })
  })
})

describe('sortRows', () => {
  const rows: Row[] = [
    { id: 1, no: '2026000066', amount: 10 },
    { id: 2, no: null, amount: null },
    { id: 3, no: '508027', amount: 2 },
    { id: 4, no: '', amount: Number.NaN },
    { id: 5, no: '9', amount: 100 },
  ]

  it('returns the incoming order when there is no sort', () => {
    expect(ids(sortRows(rows, null, getters))).toEqual([1, 2, 3, 4, 5])
  })

  it('compares document numbers number-aware', () => {
    expect(ids(sortRows(rows, { key: 'no', dir: 'asc' }, getters))).toEqual([5, 3, 1, 2, 4])
  })

  it('keeps empties (null, "", NaN) last in both directions', () => {
    expect(ids(sortRows(rows, { key: 'no', dir: 'desc' }, getters))).toEqual([1, 3, 5, 2, 4])
    expect(ids(sortRows(rows, { key: 'amount', dir: 'asc' }, getters))).toEqual([3, 1, 5, 2, 4])
    expect(ids(sortRows(rows, { key: 'amount', dir: 'desc' }, getters))).toEqual([5, 1, 3, 2, 4])
  })

  it('sorts amounts numerically, not as text', () => {
    const r: Row[] = [{ id: 1, no: null, amount: 100 }, { id: 2, no: null, amount: 9 }]
    expect(ids(sortRows(r, { key: 'amount', dir: 'asc' }, getters))).toEqual([2, 1])
  })

  it('is case-insensitive and stable on ties', () => {
    const r: Row[] = [
      { id: 1, no: null, amount: null, name: 'beta' },
      { id: 2, no: null, amount: null, name: 'Amazon' },
      { id: 3, no: null, amount: null, name: 'amazon' },
    ]
    expect(ids(sortRows(r, { key: 'name', dir: 'asc' }, getters))).toEqual([2, 3, 1])
    expect(ids(sortRows(r, { key: 'name', dir: 'desc' }, getters))).toEqual([1, 2, 3])
  })

  it('sorts ISO dates and Date objects chronologically', () => {
    const r: Row[] = [
      { id: 1, no: null, amount: null, date: '2026-10-19' },
      { id: 2, no: null, amount: null, date: '2025-01-02' },
      { id: 3, no: null, amount: null, date: null },
    ]
    expect(ids(sortRows(r, { key: 'date', dir: 'asc' }, getters))).toEqual([2, 1, 3])
    const d = [new Date('2026-01-01'), new Date('2024-01-01')]
    expect(sortRows(d, { key: 't', dir: 'asc' }, { t: (x: Date) => x })).toEqual([d[1], d[0]])
  })

  it('does not mutate the input and ignores a key without a getter', () => {
    const copy = [...rows]
    sortRows(rows, { key: 'amount', dir: 'desc' }, getters)
    expect(rows).toEqual(copy)
    expect(ids(sortRows(rows, { key: 'nope' as K, dir: 'asc' }, getters))).toEqual([1, 2, 3, 4, 5])
  })
})

describe('sortParams', () => {
  it('maps a sort to the {sort, dir} query pair, and none to {}', () => {
    expect(sortParams<K>({ key: 'amount', dir: 'desc' })).toEqual({ sort: 'amount', dir: 'desc' })
    expect(sortParams(null)).toEqual({})
  })
})

describe('SortTh', () => {
  const render = (sort: Sort<K> | null, extra: Partial<Parameters<typeof SortTh<K>>[0]> = {}) =>
    renderToStaticMarkup(
      <table><thead><tr>
        <SortTh<K> label="Amount" sortKey="amount" sort={sort} onSort={() => {}} {...extra} />
      </tr></thead></table>)

  it('marks an inactive sortable column aria-sort=none with a tooltip', () => {
    const html = render({ key: 'no', dir: 'asc' })
    expect(html).toContain('aria-sort="none"')
    expect(html).toContain('title="Sort by Amount"')
    expect(html).toContain('type="button"')
  })

  it('reports the active direction', () => {
    expect(render({ key: 'amount', dir: 'asc' })).toContain('aria-sort="ascending"')
    expect(render({ key: 'amount', dir: 'desc' })).toContain('aria-sort="descending"')
  })

  it('keeps the caller th classes and right-aligns amounts', () => {
    const html = render(null, { align: 'right', className: 'px-4 py-3 text-xs' })
    expect(html).toMatch(/<th class="[^"]*text-right[^"]*px-4 py-3 text-xs/)
    expect(html).toContain('flex-row-reverse')
  })
})
