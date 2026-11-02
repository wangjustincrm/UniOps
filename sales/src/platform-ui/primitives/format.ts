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

/** An ISO date-time in the user's local time, e.g. "Oct 30, 2026, 2:05 p.m.". */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  if (DATE_ONLY.test(iso)) return formatDate(iso)
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : DATE_TIME.format(d)
}

/** A date-only value as written ("2026-10-30"); a date-time keeps its date part. */
export function formatDate(day: string | null | undefined): string {
  if (!day) return '—'
  return day.slice(0, 10)
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

// One status → tone map for every state machine of the module; a status it
// does not name gets its tone from its words (a new enum value from /meta
// still looks right).
const TONES: Record<string, BadgeVariant> = {
  Pending: 'info', Processing: 'info', Processed: 'success', Failed: 'danger', Skipped: 'neutral', Duplicate: 'neutral',
  RUNNING: 'info', SUCCEEDED: 'success', PARTIAL: 'warning', FAILED: 'danger',
  ERROR: 'danger', WARNING: 'warning', INFO: 'neutral',
  'Ready To Send': 'info', Sending: 'info', Sent: 'success', Suppressed: 'warning', Replaced: 'neutral', Cancelled: 'neutral',
  Waiting: 'info', Accepted: 'success', 'Accepted by 945': 'success', Rejected: 'danger', Timeout: 'warning',
  'Not Required': 'neutral', Draft: 'neutral', Review: 'warning', 'Payment Hold': 'warning', 'Archive Suppressed': 'warning',
  Shipped: 'success', 'On Hold': 'warning', Active: 'success', Inactive: 'neutral', Completed: 'success',
}

export function statusTone(status: string | null | undefined): BadgeVariant {
  if (!status) return 'neutral'
  if (TONES[status]) return TONES[status]
  const s = status.toLowerCase()
  if (/(fail|reject|error|refused)/.test(s)) return 'danger'
  if (/(timeout|hold|review|partial|suppress|warn|short)/.test(s)) return 'warning'
  if (/(accept|sent|shipped|processed|success|complete|done|active)/.test(s)) return 'success'
  if (/(pending|process|running|waiting|generated|sending|ready)/.test(s)) return 'info'
  return 'neutral'
}

