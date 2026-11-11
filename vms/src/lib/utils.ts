import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// A date-only string ("2026-07-30") carries no time zone. `new Date()` parses it
// as UTC midnight, and rendering that with a local formatter anywhere west of
// Greenwich shows the PREVIOUS day — this company is UTC-4/-5, so every
// date-only field in the app displayed one day early. Reported against an
// agreement's payment schedule (expected 2026-07-30, shown as 29/07/2026), but
// it applied to invoice dates, due dates, validity windows and everything else
// stored as a plain date.
//
// Strings that carry a time are left alone: those are real instants, and
// showing them in the reader's own zone is correct.
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

export function toLocalDate(value: Date | string): Date {
  if (typeof value !== 'string') return value
  if (!DATE_ONLY.test(value)) return new Date(value)
  const [y, m, d] = value.split('-').map(Number)
  return new Date(y, m - 1, d)
}

// Company-wide display format (user decision 2026-11-11): every date shown in
// the UI reads YYYY-MMM-DD with an upper-case English month, e.g. 2026-NOV-11,
// and a date-time adds a 24-hour clock: 2026-NOV-11 14:30. Mixed formats
// (11/11/2026 vs Nov 11, 2026) were being misread as day/month swaps.
// Date inputs keep ISO (the browser owns them) and so do exported files.
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
const pad2 = (n: number) => String(n).padStart(2, '0')

export function formatDate(date: string | Date | null | undefined): string {
  if (!date) return '—'
  const d = toLocalDate(date)
  if (isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}-${MONTHS[d.getMonth()]}-${pad2(d.getDate())}`
}

export function formatDateTime(date: string | Date | null | undefined): string {
  if (!date) return '—'
  const d = toLocalDate(date)
  if (isNaN(d.getTime())) return '—'
  return `${formatDate(d)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** Today's date as YYYY-MM-DD in the browser's (the plant's) time zone.
 *  `new Date().toISOString().slice(0, 10)` is the UTC date, which rolls over
 *  at 20:00 EDT / 19:00 EST — "Today's Visits" then showed tomorrow and New
 *  Visit defaulted to tomorrow's date. */
export function localToday(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** Format an ISO timestamp as relative time. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso).getTime()
  if (isNaN(d)) return '—'
  const seconds = Math.round((Date.now() - d) / 1000)
  if (seconds < 60)         return 'just now'
  if (seconds < 3600)       return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86400)      return `${Math.round(seconds / 3600)}h ago`
  if (seconds < 86400 * 7)  return `${Math.round(seconds / 86400)}d ago`
  return formatDate(iso)
}
