import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
import {
  currentMonthISO, displayTimeZone, formatDate, formatDateTime, formatTime, isoDateInZone,
  setDisplayTimeZone, timeZoneAbbrev, toLocalDate, todayISO, zonedParts,
} from '@uniops/shell'

// Dates and times have ONE implementation, in @uniops/shell (lib/date.ts):
// display format YYYY-MMM-DD / YYYY-MMM-DD HH:mm (UI-DATE-002), instants on
// the plant's time zone from Company Settings, bare dates never shifted.
export {
  currentMonthISO, displayTimeZone, formatDate, formatDateTime, formatTime, isoDateInZone,
  setDisplayTimeZone, timeZoneAbbrev, toLocalDate, todayISO, zonedParts,
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

// The same UTC/local trap as above, on the writing side rather than the reading
// side. Every create form seeded its date field with
// `new Date().toISOString().slice(0, 10)`, which is the UTC date — so from
// 20:00 Toronto time onward, a claim raised tonight was stamped TOMORROW. It
// went to the approver, to the accounting period, and to the age of the
// document, all one day out, for the last four hours of every working day.
export function todayLocal(): string {
  return todayISO()   // the plant's date (company time zone), not the browser's
}

// Calendar year at the reader's desk. `getUTCFullYear()` rolls over at 20:00 on
// 31 December here, which pointed the budget-account lookup at the next fiscal
// year while people were still booking against this one.
export function currentYearLocal(): number {
  return Number(todayISO().slice(0, 4))
}
