import type { ReactNode } from 'react'
import type { ColumnDef } from './DataTable'
import {
  BooleanCell, CountdownCell, DateCell, DurationCell, FileCell, MoneyCell, NumberCell, PlaceCell, ReferenceCell, RefsCell, RelativeCell,
  StackedDateTimeCell, StatusCell, TagsCell, TrackingCell,
} from './cells'
import type { BadgeVariant } from './StatusBadge'
import type { RecordRef } from './cells'

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
    datetime: (key: Key<T>, header: string) => cell(key, header, (r) => <StackedDateTimeCell value={read(r, key) as string | null} />),
    relative: (key: Key<T>, header: string) => cell(key, header, (r) => <RelativeCell value={read(r, key) as string | null} />),
    countdown: (key: Key<T>, header: string) => cell(key, header, (r) => <CountdownCell value={read(r, key) as string | null} />),
    status: (key: Key<T>, header: string, opts?: { tone?: (value: string) => BadgeVariant; colour?: (value: string) => string }) =>
      cell(key, header, (r) => {
        const v = read(r, key) as string | null
        return <StatusCell value={v} tone={v && opts?.tone ? opts.tone(v) : undefined} className={v && opts?.colour ? opts.colour(v) : undefined} />
      }),
    reference: (key: Key<T>, header: string) => cell(key, header, (r) => <ReferenceCell value={read(r, key) as Value} />),
    tracking: (key: Key<T>, header: string, opts?: { url?: Key<T> }) =>
      cell(key, header, (r) => <TrackingCell value={read(r, key) as string | null} url={opts?.url ? read(r, opts.url) as string | null : undefined} />),
    duration: (id: string, header: string, from: Key<T>, to: Key<T>): ColumnDef<T, unknown> => (
      { id, header, enableSorting: false, cell: ({ row }) => <DurationCell from={read(row.original, from) as string | null} to={read(row.original, to) as string | null} /> }),
    file: (key: Key<T>, header: string, opts: { label: (row: T) => string; onDownload: (row: T) => void }) =>
      cell(key, header, (r) => <FileCell name={read(r, key) as string | null} label={opts.label(r)} onDownload={() => opts.onDownload(r)} />),
    bool: (key: Key<T>, header: string) => cell(key, header, (r) => <BooleanCell value={read(r, key) as boolean | null} />),
    tags: (key: Key<T>, header: string) => cell(key, header, (r) => <TagsCell values={read(r, key) as string[] | null} />),
    /** Where an address is (city, province; `withName`: the place's name above, e.g. a DC). Not sortable. */
    place: (id: string, header: string, address: (row: T) => Record<string, unknown> | null | undefined, opts?: { withName?: (row: T) => boolean }):
      ColumnDef<T, unknown> => ({ id, header, enableSorting: false,
        cell: ({ row }) => <PlaceCell address={address(row.original)} withName={opts?.withName?.(row.original)} /> }),
    /** Records the row belongs to, as links: the first few, "+N more" for the rest; with none, why. */
    refs: (id: string, header: string, opts: {
      refs: (row: T) => RecordRef[]; onOpen: (ref: RecordRef, row: T) => void; total?: (row: T) => number
      empty?: (row: T) => string | null | undefined; max?: number; noun?: string
    }): ColumnDef<T, unknown> => ({
      id, header, enableSorting: false, cell: ({ row }) => (
        <RefsCell refs={opts.refs(row.original)} total={opts.total?.(row.original)} onOpen={(ref) => opts.onOpen(ref, row.original)}
          empty={opts.empty?.(row.original)} max={opts.max} noun={opts.noun} />) }),
  }
}
