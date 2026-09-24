/**
 * Time formatting for the Safety module.
 *
 * `formatDate` itself is re-exported from lib/utils, which already carries the
 * repository's fix for date-only strings: `new Date('2026-08-28')` parses as
 * UTC midnight and renders as the 27th anywhere west of Greenwich, so every
 * date-only field in the app showed a day early until that was corrected.
 * Shipping a second implementation of the same rule is how the two drift.
 *
 * What lives here is what Safety needs on top: how late something is, and the
 * statutory countdown.
 */
export { formatDate } from './utils'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** Format a timestamp in the viewer's timezone. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const dt = new Date(value)
  if (Number.isNaN(dt.getTime())) return value
  return `${dt.getDate()} ${MONTHS[dt.getMonth()]} ${dt.getFullYear()}, ` +
    `${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`
}

/** Whole days between a date-only value and today, positive when overdue. */
export function daysOverdue(dueDate: string | null | undefined): number | null {
  if (!dueDate) return null
  const [y, m, d] = dueDate.slice(0, 10).split('-').map(Number)
  if (!y || !m || !d) return null
  // Compare local calendar days, not instants.
  const now = new Date()
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((today - Date.UTC(y, m - 1, d)) / 86_400_000)
}

/**
 * A countdown rendered from a deadline, computed in the browser so the clock
 * ticks without polling the server.
 */
export function countdown(dueAt: string | null | undefined, now: Date = new Date()) {
  if (!dueAt) return null
  const due = new Date(dueAt)
  if (Number.isNaN(due.getTime())) return null
  const ms = due.getTime() - now.getTime()
  const overdue = ms <= 0
  const abs = Math.abs(ms)
  const hours = Math.floor(abs / 3_600_000)
  const minutes = Math.floor((abs % 3_600_000) / 60_000)
  const days = Math.floor(hours / 24)

  let label: string
  if (days >= 1) label = `${days}d ${hours % 24}h`
  else if (hours >= 1) label = `${hours}h ${minutes}m`
  else label = `${minutes}m`

  // Thresholds match the escalation ladder in the backend sweep.
  const tone = overdue ? 'over' : hours < 4 ? 'urgent' : hours < 24 ? 'soon' : 'calm'
  return { label: overdue ? `overdue ${label}` : label, overdue, tone, hours }
}

/**
 * Today, and now, as the `<input type="date">` and `type="time"` want them.
 *
 * ★ Built from the local parts rather than `toISOString().slice(0, 10)`.
 * This plant is at UTC-4, so anything filed after 8pm would be dated
 * tomorrow — the whole module has been caught by that once already, and a
 * date that is quietly a day out on an injury report is the kind of thing an
 * inspector finds rather than we do.
 */
export function todayForInput(): string {
  return dayOf(new Date())
}

/** A Date's own calendar day, as `yyyy-mm-dd`. */
export function dayOf(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${
    String(d.getDate()).padStart(2, '0')}`
}

/**
 * The calendar day a stored value falls on, as `yyyy-mm-dd`, for comparing
 * against a date range the person picked.
 *
 * A date-only string is already a calendar day and is returned untouched —
 * parsing it through `Date` is the repository's oldest date bug. A timestamp
 * is converted to the day it happened *here*: a 9pm incident is stored as the
 * next day in UTC, and it must not fall outside a range that includes today.
 */
export function localDay(value: string | null | undefined): string | null {
  if (!value) return null
  if (!value.includes('T')) return value.slice(0, 10)
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value.slice(0, 10) : dayOf(d)
}

export function nowForInput(): string {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${
    String(d.getMinutes()).padStart(2, '0')}`
}
