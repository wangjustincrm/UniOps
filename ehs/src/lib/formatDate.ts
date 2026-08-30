/**
 * Date formatting for the Safety module.
 *
 * The one rule that matters here: a date-only string is never handed to
 * `new Date()`. `new Date('2026-08-28')` is parsed as UTC midnight, which in
 * Kingston (UTC−4, or −5 in winter) is the evening of the 27th — so every
 * date-only value renders a day early, and a 1 January value renders in the
 * previous year. This has already caught the project once across all seven
 * frontends. Date-only strings are split and formatted as text.
 *
 * Timestamps are a different matter: they carry an offset, so `new Date()` is
 * correct for them and the browser renders them in the viewer's zone.
 */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Format a date-only value (`2026-08-28`) without going through Date. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const [y, m, d] = value.slice(0, 10).split('-')
  if (!y || !m || !d) return value
  return `${Number(d)} ${MONTHS[Number(m) - 1] ?? m} ${y}`
}

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
