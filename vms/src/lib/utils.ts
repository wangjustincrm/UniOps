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

/** Today's date as YYYY-MM-DD in the browser's (the plant's) time zone.
 *  `new Date().toISOString().slice(0, 10)` is the UTC date, which rolls over
 *  at 20:00 EDT / 19:00 EST — "Today's Visits" then showed tomorrow and New
 *  Visit defaulted to tomorrow's date. */
export function localToday(d: Date = new Date()): string {
  return isoDateInZone(d)   // the plant's date (company time zone)
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
