// The one place values are turned into display text (pages use the cells;
// features may not call toLocaleString / new Date themselves — ESLint).
//
// A date-only value ("2026-10-30") is shown as given: it never goes through
// `new Date("YYYY-MM-DD")`, which reads it as UTC midnight and shows the day
// before west of Greenwich (UTC-4 here).

import { DATE_ONLY } from './dateValues'
import type { BadgeVariant } from './StatusBadge'

const DATE_TIME = new Intl.DateTimeFormat('en-CA', { dateStyle: 'medium', timeStyle: 'short' })
const TIME = new Intl.DateTimeFormat('en-CA', { timeStyle: 'medium' })
const SHORT_TIME = new Intl.DateTimeFormat('en-CA', { timeStyle: 'short' })
const LOCAL_DAY = new Intl.DateTimeFormat('en-CA', { dateStyle: 'medium' })
// A calendar day in the same style as a date-time ("Nov 18, 2026"), read in UTC
// from its own Y-M-D parts: the day never moves with the user's time zone.
const DAY = new Intl.DateTimeFormat('en-CA', { dateStyle: 'medium', timeZone: 'UTC' })

/** An ISO date-time in the user's local time, e.g. "Oct 30, 2026, 2:05 p.m.". */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  if (DATE_ONLY.test(iso)) return formatDate(iso)
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : DATE_TIME.format(d)
}

/** A moment in two parts for a narrow column: the user's local day and time. A date-only value has no time. */
export function dateAndTime(iso: string | null | undefined): { day: string; time: string | null } {
  if (!iso) return { day: '—', time: null }
  if (DATE_ONLY.test(iso)) return { day: formatDate(iso), time: null }
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? { day: iso, time: null } : { day: LOCAL_DAY.format(d), time: SHORT_TIME.format(d) }
}

/** A date-only value ("2026-10-30" → "Oct 30, 2026"), in the style of
 *  formatDateTime; a date-time keeps its date part as written. */
export function formatDate(day: string | null | undefined): string {
  if (!day) return '—'
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(day)
  if (!m) return day
  return DAY.format(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
}

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY_MS = 24 * HOUR

/** A length of time, the two largest units: "3d 4h", "2h 33m", "12m", "under 1m". */
export function formatDuration(ms: number): string {
  const t = Math.abs(ms)
  if (t < MINUTE) return 'under 1m'
  const d = Math.floor(t / DAY_MS)
  const h = Math.floor((t % DAY_MS) / HOUR)
  const m = Math.floor((t % HOUR) / MINUTE)
  if (d) return h ? `${d}d ${h}h` : `${d}d`
  if (h) return m ? `${h}h ${m}m` : `${h}h`
  return `${m}m`
}

export interface TimeFromNow {
  /** "in 2h 33m", "3d ago", "today", "in 12 days", "3 days ago". */
  text: string
  past: boolean
}

/** How far a moment is from now. A date-only value counts whole days on its own calendar day:
 *  it never goes through `new Date("YYYY-MM-DD")`. */
export function timeFromNow(iso: string | null | undefined, now: number): TimeFromNow | null {
  if (!iso) return null
  const day = DATE_ONLY.test(iso) ? /^(\d{4})-(\d{2})-(\d{2})/.exec(iso) : null
  if (day) {
    const today = new Date(now)
    const days = Math.round((Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3]))
      - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / DAY_MS)
    if (days === 0) return { text: 'today', past: false }
    const n = Math.abs(days)
    const unit = n === 1 ? 'day' : 'days'
    return days > 0 ? { text: `in ${n} ${unit}`, past: false } : { text: `${n} ${unit} ago`, past: true }
  }
  const at = new Date(iso).getTime()
  if (Number.isNaN(at)) return null
  const past = at <= now
  return { text: past ? `${formatDuration(now - at)} ago` : `in ${formatDuration(at - now)}`, past }
}

/** A date-time as ms since the epoch; null when it is not one. */
export function instant(iso: string | null | undefined): number | null {
  if (!iso) return null
  const at = new Date(iso).getTime()
  return Number.isNaN(at) ? null : at
}

/** A number in the module's style, e.g. "1,200". */
export function formatNumber(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''
  const n = Number(value)
  return Number.isFinite(n) ? n.toLocaleString('en-CA', { maximumFractionDigits: 4 }) : String(value)
}

/** The time of a moment (ms since epoch), e.g. "2:05:31 p.m.". */
export function formatTime(ms: number): string {
  return TIME.format(ms)
}

/** An amount with its currency, e.g. "1,234.50 CAD". Amounts arrive as text (exact decimals). */
export function formatMoney(amount: string | number | null | undefined, currency?: string | null): string {
  if (amount === null || amount === undefined || amount === '') return ''
  const n = Number(amount)
  if (!Number.isFinite(n)) return String(amount)
  const text = n.toLocaleString('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return currency ? `${text} ${currency}` : text
}

// The module's status → tone map, given at startup (business names stay out of this library).
// A status it does not name gets its tone from its words (a new enum value from /meta still looks right).
let tones: Record<string, BadgeVariant> = {}

export function registerStatusTones(map: Record<string, BadgeVariant>): void {
  tones = map
}

export function statusTone(status: string | null | undefined): BadgeVariant {
  if (!status) return 'neutral'
  if (tones[status]) return tones[status]
  const s = status.toLowerCase()
  if (/(fail|reject|error|refused)/.test(s)) return 'danger'
  if (/(timeout|hold|review|partial|suppress|warn|short)/.test(s)) return 'warning'
  if (/(accept|sent|shipped|processed|success|complete|done|active)/.test(s)) return 'success'
  if (/(pending|process|running|waiting|generated|sending|ready)/.test(s)) return 'info'
  return 'neutral'
}

