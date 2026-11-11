/**
 * Timezone-aware date/time helpers for the booking app.
 *
 * All wall-clock display and input must operate in the plant's time zone (the
 * company setting, `displayTimeZone()` from @uniops/shell), not browser-local
 * time.  Any user whose browser is in a different timezone would otherwise see
 * shifted times and, worse, submit silently wrong UTC values.
 *
 * The zone is read at call time — never cache it in a module-level constant:
 * main.tsx loads the company setting before the first render, but a constant
 * evaluated at import time would still hold the fallback.
 */
import { displayTimeZone, wallClockToISO } from '@/lib/utils'

/**
 * Extract HH:MM from a UTC ISO string, expressed in the plant's wall-clock time
 * (or `tz` when given).
 * e.g. "2025-07-15T14:00:00.000Z" → "10:00" (Toronto EDT = UTC-4)
 */
export function isoToHMInZone(iso: string, tz: string = displayTimeZone()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone: tz,
  }).formatToParts(new Date(iso))
  const h = parts.find((p) => p.type === 'hour')?.value ?? '00'
  const m = parts.find((p) => p.type === 'minute')?.value ?? '00'
  return `${h === '24' ? '00' : h}:${m}`
}

/**
 * Extract YYYY-MM-DD from a UTC ISO string, expressed in the plant's wall-clock
 * date (or `tz` when given).
 * e.g. "2025-07-15T03:00:00.000Z" → "2025-07-14" if tz is UTC-5
 */
export function isoToDateInZone(iso: string, tz: string = displayTimeZone()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    timeZone: tz,
  }).formatToParts(new Date(iso))
  const y = parts.find((p) => p.type === 'year')?.value ?? '2000'
  const mo = parts.find((p) => p.type === 'month')?.value ?? '01'
  const d = parts.find((p) => p.type === 'day')?.value ?? '01'
  return `${y}-${mo}-${d}`
}

/**
 * Convert a wall-clock date+time in the plant's time zone (or `tz` when given)
 * to a UTC ISO string.
 *
 * The offset-probe technique:
 *   1. Assume the wall-clock time is in UTC (naively construct a Date).
 *   2. Format that Date back in the target tz to find the apparent offset.
 *   3. Adjust by the difference between the apparent time and the input time.
 *   4. Re-check once (handles DST gaps where the first guess lands in the gap).
 *
 * The difference is taken on the full date + time, not on the clock alone: on
 * the clock alone an early-morning time ("02:00" Toronto, whose naive UTC
 * reading is 22:00 the day before) came out a whole day early.
 *
 * Example: "2025-11-03" + "01:30" in "America/Toronto" (clocks fall back at 2AM)
 * → unambiguously resolves to the first 01:30 (EDT, UTC-4) = "2025-11-03T05:30:00Z"
 */
export function zonedToISO(dateStr: string, timeStr: string, tz: string = displayTimeZone()): string {
  // Shared implementation (@uniops/shell wallClockToISO): date + time, so early
  // morning does not slip a day; DST gaps re-checked once.
  return wallClockToISO(dateStr, timeStr, tz)
}
