import { Badge } from './shell'
import type { BadgeVariant } from './StatusBadge'
import { formatDate, formatDateTime, formatMoney, statusTone } from './format'

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

/** A status as a badge; `tone` overrides the shared map (e.g. a derived order state). */
export function StatusCell({ value, tone }: { value: string | null | undefined; tone?: BadgeVariant }) {
  if (!value) return <span className="text-neutral-400">—</span>
  return <Badge variant={tone ?? statusTone(value)}>{value}</Badge>
}
