/**
 * Timezone-aware date/time helpers for the booking app.
 *
 * All wall-clock display and input must operate in DISPLAY_TZ, not browser-local
 * time.  Any user whose browser is in a different timezone would otherwise see
 * shifted times and, worse, submit silently wrong UTC values.
 */

export const DISPLAY_TZ = 'America/Toronto'

/**
 * Extract HH:MM from a UTC ISO string, expressed in DISPLAY_TZ wall-clock time.
 * e.g. "2025-07-15T14:00:00.000Z" → "10:00" (Toronto EDT = UTC-4)
 */
export function isoToHMInZone(iso: string, tz: string = DISPLAY_TZ): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: tz,
  }).formatToParts(new Date(iso))
  const h = parts.find((p) => p.type === 'hour')?.value ?? '00'
  const m = parts.find((p) => p.type === 'minute')?.value ?? '00'
  return `${h}:${m}`
}

/**
 * Extract YYYY-MM-DD from a UTC ISO string, expressed in DISPLAY_TZ wall-clock date.
 * e.g. "2025-07-15T03:00:00.000Z" → "2025-07-14" if tz is UTC-5
 */
export function isoToDateInZone(iso: string, tz: string = DISPLAY_TZ): string {
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
 * Convert a wall-clock date+time in the given timezone to a UTC ISO string.
 *
 * The offset-probe technique:
 *   1. Assume the wall-clock time is in UTC (naively construct a Date).
 *   2. Format that Date back in the target tz to find the apparent offset.
 *   3. Adjust by the difference between the apparent time and the input time.
 *   4. Re-check once (handles DST gaps where the first guess lands in the gap).
 *
 * This correctly handles DST transitions without requiring the Temporal API or
 * any third-party library.
 *
 * Example: "2025-11-03" + "01:30" in "America/Toronto" (clocks fall back at 2AM)
 * → unambiguously resolves to the first 01:30 (EDT, UTC-4) = "2025-11-03T05:30:00Z"
 */
export function zonedToISO(dateStr: string, timeStr: string, tz: string = DISPLAY_TZ): string {
  // Step 1: naive UTC milliseconds (treat wall-clock as if it were UTC)
  const naiveMs = Date.parse(`${dateStr}T${timeStr}:00Z`)

  // Step 2: format that naive point back in the target tz
  function apparentHM(ms: number): { h: number; m: number } {
    const parts = new Intl.DateTimeFormat('en-CA', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: tz,
    }).formatToParts(new Date(ms))
    return {
      h: parseInt(parts.find((p) => p.type === 'hour')?.value ?? '0', 10),
      m: parseInt(parts.find((p) => p.type === 'minute')?.value ?? '0', 10),
    }
  }

  const [inputH, inputM] = timeStr.split(':').map(Number)

  const apparent1 = apparentHM(naiveMs)
  const diffMinutes1 = (apparent1.h - inputH) * 60 + (apparent1.m - inputM)
  const adjustedMs = naiveMs - diffMinutes1 * 60_000

  // Step 3: verify the adjusted point lands at the expected wall-clock time
  // (handles DST gap: if adjustedMs falls in a gap, Intl will snap forward)
  const apparent2 = apparentHM(adjustedMs)
  const diffMinutes2 = (apparent2.h - inputH) * 60 + (apparent2.m - inputM)
  const finalMs = adjustedMs - diffMinutes2 * 60_000

  return new Date(finalMs).toISOString()
}
