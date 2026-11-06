import { Badge } from './shell'
import type { BadgeVariant } from './StatusBadge'
import { cn } from '../lib/cn'
import { formatDate, formatDateTime, formatMoney, formatNumber, statusTone, timeFromNow } from './format'
import { useNow } from './useNow'

// The standard cells (and detail values) of every list: one way to show a
// date-time, a date, an amount and a status.

export function DateTimeCell({ value }: { value: string | null | undefined }) {
  return <span className="whitespace-nowrap">{formatDateTime(value)}</span>
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

/** A status as a badge; `tone` overrides the shared map (e.g. a derived order state). */
export function StatusCell({ value, tone, className }: { value: string | null | undefined; tone?: BadgeVariant; className?: string }) {
  if (!value) return <span className="text-neutral-400">—</span>
  return <Badge variant={tone ?? statusTone(value)} className={className}>{value}</Badge>
}
