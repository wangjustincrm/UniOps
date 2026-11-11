import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatAmount(amount: number | string, currency = 'CAD'): string {
  const n = typeof amount === 'string' ? parseFloat(amount) : amount
  if (isNaN(n)) return '—'
  return new Intl.NumberFormat('en-CA', {
    style: 'currency', currency, minimumFractionDigits: 2,
  }).format(n)
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

export function timeAgo(date: string | Date | null | undefined): string {
  if (!date) return ''
  const d = toLocalDate(date)
  const diff = Date.now() - d.getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}
