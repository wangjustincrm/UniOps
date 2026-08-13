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

function toLocalDate(value: Date | string): Date {
  if (typeof value !== 'string') return value
  if (!DATE_ONLY.test(value)) return new Date(value)
  const [y, m, d] = value.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function formatDate(date: string | Date | null | undefined): string {
  if (!date) return '—'
  const d = toLocalDate(date)
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-CA', { year: 'numeric', month: 'short', day: 'numeric' })
}

export function formatDateTime(date: string | Date | null | undefined): string {
  if (!date) return '—'
  const d = toLocalDate(date)
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-CA', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
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
  return new Date(iso).toLocaleDateString('en-CA')
}
