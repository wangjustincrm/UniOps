import type { ReactNode } from 'react'
import type { ColumnDef } from './DataTable'
import {
  BooleanCell, CountdownCell, DateCell, DateTimeCell, MoneyCell, NumberCell, RelativeCell, StatusCell, TagsCell,
} from './cells'
import type { BadgeVariant } from './StatusBadge'

type Key<T> = Extract<keyof T, string>
type Value = string | number | null | undefined

/** Every list column by what it holds, so a kind of value looks the same in every list. A column
 *  with a look of its own stays a plain ColumnDef. `const col = columnsOf<OrderRead>()`. */
export function columnsOf<T>() {
  const read = (row: T, key: Key<T>) => row[key] as unknown
  const text = (key: Key<T>, header: string): ColumnDef<T, unknown> => ({ accessorKey: key, header })
  const cell = (key: Key<T>, header: string, render: (row: T) => ReactNode): ColumnDef<T, unknown> => (
    { accessorKey: key, header, cell: ({ row }) => render(row.original) })
  return {
    text,
    number: (key: Key<T>, header: string, opts?: { unit?: string }) =>
      cell(key, header, (r) => <NumberCell value={read(r, key) as Value} unit={opts?.unit} />),
    money: (key: Key<T>, header: string, opts?: { currency?: Key<T> }) =>
      cell(key, header, (r) => <MoneyCell amount={read(r, key) as Value} currency={opts?.currency ? read(r, opts.currency) as string | null : undefined} />),
    date: (key: Key<T>, header: string) => cell(key, header, (r) => <DateCell value={read(r, key) as string | null} />),
    datetime: (key: Key<T>, header: string) => cell(key, header, (r) => <DateTimeCell value={read(r, key) as string | null} />),
    relative: (key: Key<T>, header: string) => cell(key, header, (r) => <RelativeCell value={read(r, key) as string | null} />),
    countdown: (key: Key<T>, header: string) => cell(key, header, (r) => <CountdownCell value={read(r, key) as string | null} />),
    status: (key: Key<T>, header: string, opts?: { tone?: (value: string) => BadgeVariant; colour?: (value: string) => string }) =>
      cell(key, header, (r) => {
        const v = read(r, key) as string | null
        return <StatusCell value={v} tone={v && opts?.tone ? opts.tone(v) : undefined} className={v && opts?.colour ? opts.colour(v) : undefined} />
      }),
    bool: (key: Key<T>, header: string) => cell(key, header, (r) => <BooleanCell value={read(r, key) as boolean | null} />),
    tags: (key: Key<T>, header: string) => cell(key, header, (r) => <TagsCell values={read(r, key) as string[] | null} />),
  }
}
