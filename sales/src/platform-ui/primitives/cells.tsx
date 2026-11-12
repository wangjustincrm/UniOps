import { Badge } from './shell'
import type { BadgeVariant } from './StatusBadge'
import { Download, Eye } from 'lucide-react'
import { cn } from '../lib/cn'
import { CopyChip } from './Copy'
import { IconButton } from './IconButton'
import { Popover } from './Popover'
import { TrackingLink } from './TrackingLink'
import { dateAndTime, formatDate, formatDateTime, formatDuration, formatMoney, formatNumber, instant, statusTone, timeFromNow } from './format'
import { useNow } from './useNow'

// The standard cells (and detail values) of every list: one way to show a
// date-time, a date, an amount and a status.

export function DateTimeCell({ value }: { value: string | null | undefined }) {
  return <span className="whitespace-nowrap">{formatDateTime(value)}</span>
}

/** A date-time in a table: the day, the time under it. About half the width of one line. */
export function StackedDateTimeCell({ value }: { value: string | null | undefined }) {
  const { day, time } = dateAndTime(value)
  return (
    <span className="inline-flex flex-col whitespace-nowrap leading-tight" title={formatDateTime(value)}>
      <span>{day}</span>
      {time && <span className="text-xs text-neutral-500">{time}</span>}
    </span>
  )
}

export function DateCell({ value }: { value: string | null | undefined }) {
  return <span className="whitespace-nowrap">{formatDate(value)}</span>
}

export function MoneyCell({ amount, currency }: { amount: string | number | null | undefined; currency?: string | null }) {
  return <span className="whitespace-nowrap tabular-nums">{formatMoney(amount, currency)}</span>
}

/** A number, right for adding up by eye; with its unit when it has one ("1,200 EA"). */
export function NumberCell({ value, unit }: { value: string | number | null | undefined; unit?: string }) {
  const text = formatNumber(value)
  return <span className="whitespace-nowrap tabular-nums">{text}{text && unit ? ` ${unit}` : ''}</span>
}

export function BooleanCell({ value }: { value: boolean | null | undefined }) {
  if (value === null || value === undefined) return null
  return <span>{value ? 'Yes' : 'No'}</span>
}

/** How long ago, or how long until: "3d ago", "in 2h 33m". The exact time is in the tooltip. */
export function RelativeCell({ value }: { value: string | null | undefined }) {
  const t = timeFromNow(value, useNow())
  if (!t) return <span className="text-neutral-400">—</span>
  return <span className="whitespace-nowrap" title={formatDateTime(value)}>{t.text}</span>
}

/** A time still to come: "in 2h 33m", and red once it has passed ("2h late"). */
export function CountdownCell({ value, className }: { value: string | null | undefined; className?: string }) {
  const t = timeFromNow(value, useNow())
  if (!t) return <span className="text-neutral-400">—</span>
  return (
    <span title={formatDateTime(value)} className={cn('whitespace-nowrap', t.past ? 'font-medium text-danger-600' : 'text-primary-700', className)}>
      {t.past ? t.text.replace(/ ago$/, ' late') : t.text}
    </span>
  )
}

/** Several short values side by side. */
export function TagsCell({ values }: { values: (string | null | undefined)[] | null | undefined }) {
  const tags = (values ?? []).filter((v): v is string => !!v)
  return <span className="inline-flex flex-wrap gap-1">{tags.map((t) => <Badge key={t} variant="neutral">{t}</Badge>)}</span>
}

/** A code a person copies: a lot, a reference. */
export function ReferenceCell({ value }: { value: string | number | null | undefined }) {
  if (value === null || value === undefined || value === '') return <span className="text-neutral-400">—</span>
  return <CopyChip text={String(value)} />
}

/** A tracking number with the carrier's link when there is one. */
export function TrackingCell({ value, url }: { value: string | null | undefined; url?: string | null }) {
  if (!value) return <span className="text-neutral-400">—</span>
  return <TrackingLink number={value} url={url} />
}

/** How long something took: "3d 4h", "2h 33m". */
export function DurationCell({ from, to }: { from: string | null | undefined; to: string | null | undefined }) {
  const start = instant(from)
  const end = instant(to)
  if (start === null || end === null) return <span className="text-neutral-400">—</span>
  return <span className="whitespace-nowrap tabular-nums">{formatDuration(end - start)}</span>
}

/** A file: its name, and the way to get it. */
export function FileCell({ name, label, onDownload, onView }:
  { name: string | null | undefined; label: string; onDownload: () => void; onView?: () => void }) {
  return (
    <span className="flex items-center gap-1">
      <span className="min-w-0 break-all">{name ?? '—'}</span>
      {onView && <IconButton label={label.replace(/^Download\b/, 'View')} icon={<Eye className="h-4 w-4" />} onClick={onView} />}
      <IconButton label={label} icon={<Download className="h-4 w-4" />} onClick={onDownload} />
    </span>
  )
}

/** A status as a badge; `tone` overrides the shared map (e.g. a derived order state). */
export function StatusCell({ value, tone, className }: { value: string | null | undefined; tone?: BadgeVariant; className?: string }) {
  if (!value) return <span className="text-neutral-400">—</span>
  return <Badge variant={tone ?? statusTone(value)} className={className}>{value}</Badge>
}

/** A record a row points at (an order, a transfer): what to open and what to call it. */
export interface RecordRef { id: number; label: string }

const refButton = 'whitespace-nowrap rounded px-1 text-left text-primary-700 underline-offset-2 hover:underline focus-visible:outline-2'

/** Several records as links (user 2026-11-09): the first `max` in the cell, "+N more" opens all of them.
 *  `total` is how many there are in all when only the first ones came; with none, `empty` says why.
 *  A click opens the record and never the row (DataTable's clickOpensRow). */
export function RefsCell({ refs, total, onOpen, empty, max = 2, noun = 'record' }: {
  refs: RecordRef[]; total?: number; onOpen: (ref: RecordRef) => void; empty?: string | null; max?: number; noun?: string
}) {
  const all = Math.max(total ?? 0, refs.length)
  if (all === 0) return <span className="text-xs text-neutral-500">{empty ?? '—'}</span>
  const link = (r: RecordRef) => (
    <button key={r.id} type="button" className={refButton} onClick={() => onOpen(r)}>{r.label}</button>
  )
  const rest = all - Math.min(max, refs.length)
  // e.g. "3 orders"; one: "1 order"
  return (
    <span className="flex flex-wrap items-center gap-x-1">
      {refs.slice(0, max).map(link)}
      {rest > 0 && (
        <Popover align="start" className="max-h-72 w-64 overflow-y-auto"
          trigger={<button type="button" className="whitespace-nowrap rounded bg-neutral-100 px-1.5 text-xs text-neutral-700 hover:bg-neutral-200"
            aria-label={`${rest} more ${rest === 1 ? noun : `${noun}s`}`}>+{rest} more</button>}>
          <p className="mb-1 text-xs font-medium text-neutral-500">{all} {noun}s</p>
          <ul className="flex flex-col">{refs.map((r) => <li key={r.id}>{link(r)}</li>)}</ul>
          {all > refs.length && <p className="mt-1 text-xs text-neutral-500">and {all - refs.length} more: search by the {noun}.</p>}
        </Popover>
      )}
    </span>
  )
}

/** Where an address is, in a list: "City, PR" (and, with `withName`, the place's name above it: a DC). */
export function PlaceCell({ address, withName }: { address: Record<string, unknown> | null | undefined; withName?: boolean }) {
  const a = address ?? {}
  const text = (k: string) => (typeof a[k] === 'string' ? (a[k] as string).trim() : '')
  const where = [text('city'), text('province_code') || text('province')].filter(Boolean).join(', ')
  const name = withName ? text('name') : ''
  if (!where && !name) return <span className="text-neutral-400">—</span>
  return (
    <span className="inline-flex flex-col leading-tight" title={[name, where, text('zip')].filter(Boolean).join(' · ')}>
      {name && <span className="line-clamp-1">{name}</span>}
      <span className={name ? 'text-xs text-neutral-500' : 'whitespace-nowrap'}>{where || '—'}</span>
    </span>
  )
}
