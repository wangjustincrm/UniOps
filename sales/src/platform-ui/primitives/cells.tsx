import { Badge } from './shell'
import type { BadgeVariant } from './StatusBadge'
import { Download } from 'lucide-react'
import { cn } from '../lib/cn'
import { CopyChip } from './Copy'
import { IconButton } from './IconButton'
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
export function FileCell({ name, label, onDownload }: { name: string | null | undefined; label: string; onDownload: () => void }) {
  return (
    <span className="flex items-center gap-1">
      <span className="min-w-0 break-all">{name ?? '—'}</span>
      <IconButton label={label} icon={<Download className="h-4 w-4" />} onClick={onDownload} />
    </span>
  )
}

/** A status as a badge; `tone` overrides the shared map (e.g. a derived order state). */
export function StatusCell({ value, tone, className }: { value: string | null | undefined; tone?: BadgeVariant; className?: string }) {
  if (!value) return <span className="text-neutral-400">—</span>
  return <Badge variant={tone ?? statusTone(value)} className={className}>{value}</Badge>
}
