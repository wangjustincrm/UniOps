import { useMemo, useState, type ReactNode } from 'react'
import { DataTable, type ColumnDef, type RowAttrs, type SortValue } from './DataTable'

export interface LocalTableProps<T> {
  columns: ColumnDef<T, unknown>[]
  rows: T[]
  rowKey: (row: T, index: number) => string | number
  /** The table's accessible name. */
  ariaLabel: string
  /** Column ids sortable in the browser (by the column's accessorKey value). */
  sortable?: string[]
  initialSort?: SortValue
  /** One cell per column under the rows (e.g. totals). */
  footer?: ReactNode[]
  density?: 'normal' | 'compact'
  emptyTitle?: string
  rowAttrs?: (row: T) => RowAttrs
  onRowClick?: (row: T) => void
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

function valueOf<T>(row: T, column: ColumnDef<T, unknown> | undefined): unknown {
  const key = (column as { accessorKey?: string } | undefined)?.accessorKey
  return key ? (row as Record<string, unknown>)[key] : undefined
}

function compare(a: unknown, b: unknown): number {
  if (a == null && b == null) return 0
  if (a == null) return 1  // empty last, both ways
  if (b == null) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return collator.compare(String(a), String(b))
}

/** Rows already in memory — a detail panel (order lines, shipments, invoice
 *  lines, a pass report): the list table's look, header, empty state and
 *  accessibility, with sorting in the browser and an optional footer. Compact
 *  by default. Never fetches; for a server list use QueryTable / ListPage. */
export function LocalTable<T>({
  columns, rows, rowKey, ariaLabel, sortable = [], initialSort, footer, density = 'compact', emptyTitle = 'Nothing here',
  rowAttrs, onRowClick,
}: LocalTableProps<T>) {
  const [sort, setSort] = useState<SortValue | null>(initialSort ?? null)
  const keyed = useMemo(() => rows.map((row, i) => ({ row, key: rowKey(row, i) })), [rows, rowKey])
  const sorted = useMemo(() => {
    if (!sort) return keyed
    const col = columns.find((c) => (c.id ?? (c as { accessorKey?: string }).accessorKey) === sort.id)
    return [...keyed].sort((x, y) => compare(valueOf(x.row, col), valueOf(y.row, col)) * (sort.desc ? -1 : 1))
  }, [keyed, sort, columns])
  const byRow = useMemo(() => new Map(sorted.map((x) => [x.row, x.key])), [sorted])
  return (
    <DataTable<T>
      columns={columns}
      rows={sorted.map((x) => x.row)}
      rowKey={(row) => byRow.get(row) ?? ''}
      ariaLabel={ariaLabel}
      density={density}
      footer={footer}
      emptyTitle={emptyTitle}
      rowAttrs={rowAttrs}
      onRowClick={onRowClick}
      sort={sort}
      sortable={sortable}
      onSortChange={sortable.length ? setSort : undefined}
    />
  )
}
