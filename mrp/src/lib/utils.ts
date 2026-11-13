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

