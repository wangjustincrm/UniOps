import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
import {
  currentMonthISO, displayTimeZone, formatDate, formatDateTime, formatTime, isoDateInZone,
  setDisplayTimeZone, timeZoneAbbrev, toLocalDate, todayISO, wallClockToISO, zonedParts,
} from '@uniops/shell'

// Dates and times have ONE implementation, in @uniops/shell (lib/date.ts):
// display format YYYY-MMM-DD / YYYY-MMM-DD HH:mm (UI-DATE-002), instants on
// the plant's time zone from Company Settings, bare dates never shifted.
export {
  currentMonthISO, displayTimeZone, formatDate, formatDateTime, formatTime, isoDateInZone,
  setDisplayTimeZone, timeZoneAbbrev, toLocalDate, todayISO, wallClockToISO, zonedParts,
}

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
