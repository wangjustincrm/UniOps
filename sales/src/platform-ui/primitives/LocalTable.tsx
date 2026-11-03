import { useMemo, useState, type ReactNode } from 'react'
import { DataTable, type ColumnDef, type RowAttrs, type SortValue } from './DataTable'
import { ColumnChooser } from './ColumnChooser'

export interface LocalTableProps<T> {
  columns: ColumnDef<T, unknown>[]
  rows: T[]
  rowKey: (row: T, index: number) => string | number
  /** The table's accessible name. */
  ariaLabel: string
  /** Its rows are being fetched again (the bar on the card's edge). */
  fetching?: boolean
  /** Column ids sortable in the browser (by the column's accessorKey value). */
  sortable?: string[]
  initialSort?: SortValue
  /** One cell per column under the rows (e.g. totals). */
  footer?: ReactNode[]
  density?: 'normal' | 'compact'
  emptyTitle?: string
  rowAttrs?: (row: T) => RowAttrs
  onRowClick?: (row: T) => void
  /** A row's detail under it (DataTable `expand`). */
  expand?: (row: T) => ReactNode | null
  /** The eye in the header: these column ids start hidden and can be shown
   *  (a wide panel stays readable on a laptop). The footer follows the columns. */
  initiallyHidden?: string[]
}

const idOf = <T,>(c: ColumnDef<T, unknown>): string => c.id ?? (c as { accessorKey?: string }).accessorKey ?? ''

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
  rowAttrs, onRowClick, expand, initiallyHidden, fetching,
}: LocalTableProps<T>) {
  const [sort, setSort] = useState<SortValue | null>(initialSort ?? null)
  const [hidden, setHidden] = useState<string[]>(initiallyHidden ?? [])
  const shown = columns.map((c, i) => ({ c, i })).filter(({ c }) => !hidden.includes(idOf(c)))
  const keyed = useMemo(() => rows.map((row, i) => ({ row, key: rowKey(row, i) })), [rows, rowKey])
  const sorted = useMemo(() => {
    if (!sort) return keyed
    const col = columns.find((c) => (c.id ?? (c as { accessorKey?: string }).accessorKey) === sort.id)
    return [...keyed].sort((x, y) => compare(valueOf(x.row, col), valueOf(y.row, col)) * (sort.desc ? -1 : 1))
  }, [keyed, sort, columns])
  const byRow = useMemo(() => new Map(sorted.map((x) => [x.row, x.key])), [sorted])
  return (
    <DataTable<T>
      columns={initiallyHidden ? shown.map((x) => x.c) : columns}
      rows={sorted.map((x) => x.row)}
      rowKey={(row) => byRow.get(row) ?? ''}
      ariaLabel={ariaLabel}
      fetching={fetching}
      density={density}
      footer={footer && initiallyHidden ? shown.map((x) => footer[x.i]) : footer}
      expand={expand}
      leadingHeader={initiallyHidden ? (
        <ColumnChooser columns={columns.map((c) => ({ id: idOf(c), label: typeof c.header === 'string' ? c.header : idOf(c) }))}
          hidden={hidden} onChange={setHidden} />
      ) : undefined}
      emptyTitle={emptyTitle}
      rowAttrs={rowAttrs}
      onRowClick={onRowClick}
      sort={sort}
      sortable={sortable}
      onSortChange={sortable.length ? setSort : undefined}
    />
  )
}
