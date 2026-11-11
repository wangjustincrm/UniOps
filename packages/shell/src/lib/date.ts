// Dates and times for every UniOps web app — the one implementation; each
// app's src/lib/utils.ts re-exports it.
//
// Display format (UI-DATE-002, user decision 2026-11-11): dates YYYY-MMM-DD
// with an upper-case English month (2026-NOV-11); date-times add a 24-hour
// clock (2026-NOV-11 14:30). Mixed formats were being misread.
//
// Time zone: the plant's, from the company setting (Portal → Admin → Company
// Settings), NOT the browser's. Each app calls loadDisplayTimeZone() before
// its first render; until then (or if that fails) the last zone seen on this
// device is used, and failing that America/Toronto.
//
// Two kinds of value:
//  * a bare 'YYYY-MM-DD' string is a calendar date (due date, invoice date):
//    printed as is, never shifted by any zone;
//  * anything else (ISO timestamp, Date) is an instant: shown on the plant's
//    clock.
// Date inputs keep ISO values; use todayISO() / isoDateInZone() for them.

export const DEFAULT_TIME_ZONE = 'America/Toronto'
const STORAGE_KEY = 'uniops.timeZone'
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/
const pad2 = (n: number) => String(n).padStart(2, '0')

function isValidTimeZone(tz: unknown): tz is string {
  if (typeof tz !== 'string' || !tz) return false
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz })
    return true
  } catch {
    return false
  }
}

function initialZone(): string {
  try {
    const saved = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (isValidTimeZone(saved)) return saved
  } catch { /* storage blocked */ }
  return DEFAULT_TIME_ZONE
}

let zone = initialZone()
let partsFmt: Intl.DateTimeFormat | null = null

/** Adopt the company time zone (an IANA name). Invalid values are ignored. */
export function setDisplayTimeZone(tz: string | null | undefined): void {
  if (!isValidTimeZone(tz) || tz === zone) return
  zone = tz
  partsFmt = null
  try { globalThis.localStorage?.setItem(STORAGE_KEY, tz) } catch { /* ignore */ }
}

export function displayTimeZone(): string {
  return zone
}

/** Fetch the company time zone from the public branding endpoint (no sign-in
 *  needed) and adopt it. Never rejects; gives up after `timeoutMs` so a slow
 *  API does not hold the first paint. */
export async function loadDisplayTimeZone(brandingUrl: string, timeoutMs = 1500): Promise<void> {
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null
  const timer = setTimeout(() => ctrl?.abort(), timeoutMs)
  try {
    const res = await fetch(brandingUrl, { signal: ctrl?.signal, credentials: 'omit' })
    if (res.ok) setDisplayTimeZone((await res.json())?.timezone)
  } catch { /* keep the cached / default zone */ } finally {
    clearTimeout(timer)
  }
}

interface ZonedParts { year: number; month: number; day: number; hour: number; minute: number }

/** Wall-clock fields of an instant in the plant's zone. */
export function zonedParts(d: Date): ZonedParts {
  if (!partsFmt) {
    partsFmt = new Intl.DateTimeFormat('en-US', {
      timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    })
  }
  const p: Record<string, number> = {}
  for (const part of partsFmt.formatToParts(d)) {
    if (part.type !== 'literal') p[part.type] = Number(part.value)
  }
  return { year: p.year, month: p.month, day: p.day, hour: p.hour === 24 ? 0 : p.hour, minute: p.minute }
}

/** Parse for comparisons/sorting: a bare date becomes local midnight (so it
 *  never slips a day); anything else is `new Date(value)`. */
export function toLocalDate(value: Date | string): Date {
  if (typeof value !== 'string') return value
  if (!DATE_ONLY.test(value)) return new Date(value)
  const [y, m, d] = value.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function calendar(y: number, m: number, d: number): string {
  return `${y}-${MONTHS[m - 1]}-${pad2(d)}`
}

/** '2026-NOV-11'. '—' for empty or unparseable input. */
export function formatDate(date: string | Date | null | undefined): string {
  if (!date) return '—'
  if (typeof date === 'string' && DATE_ONLY.test(date)) {
    const [y, m, d] = date.split('-').map(Number)
    return m >= 1 && m <= 12 ? calendar(y, m, d) : '—'
  }
  const inst = typeof date === 'string' ? new Date(date) : date
  if (isNaN(inst.getTime())) return '—'
  const p = zonedParts(inst)
  return calendar(p.year, p.month, p.day)
}

/** '2026-NOV-11 14:30' on the plant's clock. A bare date gets 00:00. */
export function formatDateTime(date: string | Date | null | undefined): string {
  if (!date) return '—'
  if (typeof date === 'string' && DATE_ONLY.test(date)) {
    const day = formatDate(date)
    return day === '—' ? day : `${day} 00:00`
  }
  const inst = typeof date === 'string' ? new Date(date) : date
  if (isNaN(inst.getTime())) return '—'
  const p = zonedParts(inst)
  return `${calendar(p.year, p.month, p.day)} ${pad2(p.hour)}:${pad2(p.minute)}`
}

/** '14:30' on the plant's clock. */
export function formatTime(date: string | Date | null | undefined): string {
  if (!date) return '—'
  const inst = typeof date === 'string' ? new Date(date) : date
  if (isNaN(inst.getTime())) return '—'
  const p = zonedParts(inst)
  return `${pad2(p.hour)}:${pad2(p.minute)}`
}

/** 'EST' / 'EDT' (the plant zone's abbreviation at that instant). */
export function timeZoneAbbrev(date: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'short' })
      .formatToParts(date).find((p) => p.type === 'timeZoneName')?.value ?? ''
  } catch {
    return ''
  }
}

/** The plant's calendar date of an instant, as 'YYYY-MM-DD' (input values,
 *  comparisons with due dates, query parameters). */
export function isoDateInZone(d: Date): string {
  const p = zonedParts(d)
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}`
}

/** Today at the plant, 'YYYY-MM-DD'. Not `toISOString().slice(0, 10)`, which
 *  is the UTC date and turns into tomorrow at 19:00/20:00 in Toronto. */
export function todayISO(): string {
  return isoDateInZone(new Date())
}

/** Current plant month, 'YYYY-MM'. */
export function currentMonthISO(): string {
  return todayISO().slice(0, 7)
}
