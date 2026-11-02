import { useId, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Button } from './shell'
import { LocalTable } from './LocalTable'
import type { ColumnDef } from './DataTable'

export interface EditCell<T> {
  row: T
  index: number
  /** Merge a change into this row (the table calls onChange with all rows). */
  update: (patch: Partial<T>) => void
  /** The row has an error: editors set aria-invalid. */
  invalid: boolean
}

export interface EditableColumn<T> {
  id: string
  header: ReactNode
  /** An editor built from the field primitives, or a read-only value. */
  cell: (ctx: EditCell<T>) => ReactNode
}

export interface EditableTableProps<T> {
  rows: T[]
  onChange: (rows: T[]) => void
  columns: EditableColumn<T>[]
  ariaLabel: string
  rowKey?: (row: T, index: number) => string | number
  /** A row's problem, shown in the row (and its editors marked invalid). */
  errors?: (row: T, index: number) => string | null | undefined
  /** With it: an "Add" button that appends this new row. */
  onAdd?: () => T
  addLabel?: string
  /** Rows may be removed (never below `minRows`). */
  removable?: boolean
  minRows?: number
  footer?: ReactNode[]
  disabled?: boolean
}

type Indexed<T> = { row: T; index: number }

/** Rows edited in place — manual order lines, delivered quantities, invoice
 *  lines: the LocalTable look with editors in the cells, row add / remove and
 *  a per-row error. Use the column builders below, or any field primitive. */
export function EditableTable<T>({
  rows, onChange, columns, ariaLabel, rowKey = (_, i) => i, errors, onAdd, addLabel = 'Add line', removable,
  minRows = 1, footer, disabled,
}: EditableTableProps<T>) {
  const errId = useId()
  const update = (i: number, patch: Partial<T>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const remove = (i: number) => onChange(rows.filter((_, j) => j !== i))
  const problem = (x: Indexed<T>) => errors?.(x.row, x.index) || null
  const defs: ColumnDef<Indexed<T>, unknown>[] = columns.map((c) => ({
    id: c.id, header: () => c.header,
    cell: ({ row: { original: x } }) => c.cell({ row: x.row, index: x.index, update: (p) => update(x.index, p), invalid: !!problem(x) }),
  }))
  if (errors) {
    defs.push({ id: '__problem', header: () => <span className="sr-only">Problem</span>, cell: ({ row: { original: x } }) => {
      const p = problem(x)
      return p ? <span id={`${errId}-${x.index}`} role="alert" className="text-danger-600">{p}</span> : null
    } })
  }
  if (removable) {
    defs.push({ id: '__remove', header: () => <span className="sr-only">Remove</span>, cell: ({ row: { original: x } }) => (
      <button type="button" aria-label={`Remove line ${x.index + 1}`} disabled={disabled || rows.length <= minRows}
        onClick={() => remove(x.index)} className="rounded p-1 text-neutral-400 hover:text-danger-600 disabled:opacity-30">
        <X className="h-3.5 w-3.5" />
      </button>
    ) })
  }
  return (
    <div className="flex flex-col gap-2">
      <LocalTable<Indexed<T>>
        columns={defs}
        rows={rows.map((row, index) => ({ row, index }))}
        rowKey={(x) => rowKey(x.row, x.index)}
        ariaLabel={ariaLabel}
        footer={footer && [...footer, ...(errors ? [null] : []), ...(removable ? [null] : [])]}
        rowAttrs={(x) => (problem(x) ? { className: 'bg-danger-50/40', 'data-invalid': true } : {})}
      />
      {onAdd && (
        <div><Button type="button" variant="secondary" size="sm" disabled={disabled} onClick={() => onChange([...rows, onAdd()])}>{addLabel}</Button></div>
      )}
    </div>
  )
}
