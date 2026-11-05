/**
 * Click-to-sort list headers, shared by every UniOps list.
 *
 * Click a header to sort ascending, click it again to reverse; another header
 * starts ascending. ↕ marks a sortable inactive column, ↑ / ↓ the active one.
 *
 * Two ways to use it:
 *
 *   Client-side — the list is loaded whole, so sorting the rows on the page is
 *   exact:
 *     const { rows, sort, onSort } = useSortableRows(data, {
 *       number: (r) => r.number, amount: (r) => Number(r.amount),
 *     })
 *     <SortTh label="Number" sortKey="number" sort={sort} onSort={onSort} />
 *
 *   Server-side — the list is paged by the API, so the API must sort (sorting
 *   the 20 rows on screen would be wrong). Keep the state with useSort, send
 *   sortParams(sort) with the query, and go back to page 1 on change:
 *     const [sort, onSort] = useSort<Key>(null, () => setPage(1))
 *     api.list({ ...filters, ...sortParams(sort) })
 *
 * Empty values (null, undefined, '', NaN) sit at the bottom in BOTH directions
 * — otherwise sorting by a date puts a block of "—" on top. Text compares
 * number-aware and case-insensitive ("508027" before "2026000066", "amazon"
 * with "Amazon"). Ties keep their incoming order.
 */
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import { cn } from '../lib/cn'

export type SortDir = 'asc' | 'desc'
export interface Sort<K extends string = string> { key: K; dir: SortDir }

/** A cell value to sort by. Dates may be ISO strings (they sort as text) or Date. */
export type SortValue = string | number | boolean | Date | null | undefined
export type SortGetters<T, K extends string> = Record<K, (row: T) => SortValue>

/** Next state after a header click. */
export function nextSort<K extends string>(current: Sort<K> | null, key: K): Sort<K> {
  return current?.key === key
    ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: 'asc' }
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

function normalize(v: SortValue): string | number | null {
  if (v === null || v === undefined) return null
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : v.getTime()
  if (typeof v === 'boolean') return v ? 1 : 0
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  return v.trim() === '' ? null : v
}

/** Compare two values ascending; empties are NOT handled here. */
function compare(a: string | number, b: string | number): number {
  return typeof a === 'number' && typeof b === 'number'
    ? a - b
    : collator.compare(String(a), String(b))
}

/** Pure sort; returns `rows` unchanged when there is no sort or no getter for the key. */
export function sortRows<T, K extends string>(
  rows: readonly T[], sort: Sort<K> | null, getters: Partial<SortGetters<T, K>>,
): T[] {
  const get = sort ? getters[sort.key] : undefined
  if (!sort || !get) return rows as T[]
  const sign = sort.dir === 'asc' ? 1 : -1
  return rows
    .map((row, i) => ({ row, i, v: normalize(get(row)) }))
    .sort((a, b) => {
      if (a.v === null || b.v === null) {
        if (a.v === b.v) return a.i - b.i
        return a.v === null ? 1 : -1                  // empties last, either way
      }
      const c = compare(a.v, b.v)
      return c !== 0 ? sign * c : a.i - b.i           // stable on ties
    })
    .map((x) => x.row)
}

/**
 * Sort state for a header row. `onChange` runs after every click — a
 * server-paged list passes `() => setPage(1)` there.
 */
export function useSort<K extends string>(
  initial: Sort<K> | null = null, onChange?: (s: Sort<K>) => void,
): [Sort<K> | null, (key: K) => void, (s: Sort<K> | null) => void] {
  const [sort, setSort] = useState<Sort<K> | null>(initial)
  const onSort = useCallback((key: K) => {
    const next = nextSort(sort, key)
    setSort(next)
    onChange?.(next)
  }, [sort, onChange])
  return [sort, onSort, setSort]
}

/** Client-side sorting of a whole-loaded list. `getters` may be an inline object. */
export function useSortableRows<T, K extends string>(
  rows: readonly T[] | undefined, getters: SortGetters<T, K>, initial: Sort<K> | null = null,
) {
  const [sort, onSort, setSort] = useSort<K>(initial)
  // getters is usually an inline literal; only the active key's getter matters,
  // and it is read fresh on every row change, so it is left out of the deps.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const sorted = useMemo(() => sortRows(rows ?? [], sort, getters), [rows, sort])
  return { rows: sorted, sort, onSort, setSort }
}

/**
 * Query params for a server-sorted list: `{ sort, dir }`, or `{}` for the API's
 * default order. Every UniOps list endpoint that sorts takes this pair.
 */
export function sortParams<K extends string>(sort: Sort<K> | null): { sort?: K; dir?: SortDir } {
  return sort ? { sort: sort.key, dir: sort.dir } : {}
}

export interface SortThProps<K extends string> {
  /** Header text; `children` wins when both are given. */
  label?: ReactNode
  children?: ReactNode
  sortKey: K
  sort: Sort<K> | null
  onSort: (key: K) => void
  /** Right-aligned column (amounts): the arrow goes on the left of the label. */
  align?: 'left' | 'right' | 'center'
  /** Classes for the <th> — pass the table's usual header-cell classes. */
  className?: string
  /** Tooltip; defaults to "Sort by <label>" when label is a string. */
  title?: string
  colSpan?: number
  rowSpan?: number
}

/** A header cell that sorts on click. Keeps the table's own <th> styling. */
export function SortTh<K extends string>({
  label, children, sortKey, sort, onSort, align = 'left', className, title, colSpan, rowSpan,
}: SortThProps<K>) {
  const active = sort?.key === sortKey
  const Icon = !active ? ArrowUpDown : sort!.dir === 'asc' ? ArrowUp : ArrowDown
  const text = children ?? label
  return (
    <th className={cn(align === 'right' && 'text-right', align === 'center' && 'text-center', className)}
        colSpan={colSpan} rowSpan={rowSpan}
        aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" onClick={() => onSort(sortKey)}
              title={title ?? (typeof text === 'string' ? `Sort by ${text}` : undefined)}
              className={cn(
                'inline-flex items-center gap-1 select-none whitespace-nowrap',
                // preflight resets text-transform on buttons; keep UPPERCASE headers
                '[text-transform:inherit] hover:text-neutral-800',
                align === 'right' && 'flex-row-reverse',
                active && 'text-neutral-900')}>
        {text}
        <Icon aria-hidden className={cn('h-3.5 w-3.5 shrink-0', active ? 'text-primary-700' : 'text-neutral-300')} />
      </button>
    </th>
  )
}
