import type { ReactNode } from 'react'
import { DateInput, NumberInput } from './FieldInputs'
import { Input } from './shell'
import { Select, type SelectOption } from './Select'
import type { EditableColumn } from './EditableTable'

// ── column builders over the field primitives ───────────────────────────────

interface FieldOpts<T> {
  /** The input's accessible name for this row. */
  label: (row: T, index: number) => string
  get: (row: T) => string | number | null | undefined
  set: (value: string, row: T) => Partial<T>
  className?: string
  disabled?: boolean
}

export function numberColumn<T>(id: string, header: ReactNode, o: FieldOpts<T> & { min?: number; max?: (row: T) => number | undefined }): EditableColumn<T> {
  return { id, header, cell: ({ row, index, update, invalid }) => (
    <NumberInput aria-label={o.label(row, index)} aria-invalid={invalid || undefined} min={o.min} max={o.max?.(row)}
      value={o.get(row) ?? ''} disabled={o.disabled} className={o.className ?? 'w-24'} onChange={(e) => update(o.set(e.target.value, row))} />
  ) }
}

export function dateColumn<T>(id: string, header: ReactNode, o: FieldOpts<T>): EditableColumn<T> {
  return { id, header, cell: ({ row, index, update, invalid }) => (
    <DateInput aria-label={o.label(row, index)} aria-invalid={invalid || undefined} value={o.get(row) ?? ''} disabled={o.disabled}
      className={o.className} onChange={(e) => update(o.set(e.target.value, row))} />
  ) }
}

export function textColumn<T>(id: string, header: ReactNode, o: FieldOpts<T>): EditableColumn<T> {
  return { id, header, cell: ({ row, index, update, invalid }) => (
    <Input aria-label={o.label(row, index)} aria-invalid={invalid || undefined} value={o.get(row) ?? ''} disabled={o.disabled}
      className={o.className ?? 'w-28'} onChange={(e) => update(o.set(e.target.value, row))} />
  ) }
}

export function readColumn<T>(id: string, header: ReactNode, value: (row: T) => ReactNode): EditableColumn<T> {
  return { id, header, cell: ({ row }) => value(row) }
}

/** A choice from options that depend on the row (e.g. the units of the row's
 *  item); disabled while the row offers none. */
export function selectColumn<T>(id: string, header: ReactNode, o: Omit<FieldOpts<T>, 'get' | 'set'> & {
  get: (row: T) => string | null | undefined
  set: (value: string | null, row: T) => Partial<T>
  options: (row: T) => SelectOption[]
  placeholder?: string
}): EditableColumn<T> {
  return { id, header, cell: ({ row, index, update, invalid }) => {
    const options = o.options(row)
    return (
      <Select aria-label={o.label(row, index)} aria-invalid={invalid || undefined} value={o.get(row) ?? null} options={options}
        placeholder={o.placeholder ?? '—'} disabled={o.disabled || options.length === 0} className={o.className ?? 'w-36'}
        onChange={(v) => update(o.set(v, row))} />
    )
  } }
}
