// Date and date-time values between the server and the inputs.
//
// A date-only value ("2026-10-30") is a plain string from end to end: it
// never goes through `new Date("YYYY-MM-DD")`, which reads it as UTC midnight
// and shows the day before west of Greenwich (UTC-4 here).
// A date-time is ISO with a zone on the server; the input shows it in the
// user's local time ("2026-10-30T14:05").

export const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

export const isDateOnly = (s: string): boolean => DATE_ONLY.test(s)

/** ISO date-time (any zone) → the local "YYYY-MM-DDTHH:mm" an input shows. */
export function isoToLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

/** Local "YYYY-MM-DDTHH:mm" → ISO in UTC (the zone travels with it). */
export function localInputToIso(local: string): string | null {
  if (!local) return null
  const d = new Date(local)  // has a time, so it is read as local time
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

/** Today in the viewer's own calendar as "YYYY-MM-DD" (never via UTC:
 *  toISOString() gives tomorrow's date in the evening west of Greenwich). */
export function todayDate(now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`
}
