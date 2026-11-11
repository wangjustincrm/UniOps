// Timestamps (real instants, ISO with offset) shown on the plant's clock —
// the company time zone (Portal → Admin → Company Settings), whatever zone the
// reader's laptop is set to. Date-only strings ("YYYY-MM-DD") must NOT come
// through here: they have no instant, and new Date() would read them as UTC
// midnight (one day early at UTC-4). Show those as the string itself.

import { formatDateTime, timeZoneAbbrev, todayISO } from './utils'

/** "2026-OCT-14 17:05 EDT" (company-wide format, see lib/utils) on the plant's
 *  clock; "—" for null/invalid. (The name is historical — the zone is the
 *  company setting, read at call time.) */
export function formatDateTimeToronto(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  // Keep the zone tag ("EDT"/"EST"): this module also shows NC-clock times,
  // and an untagged plant time is easy to mistake for one of those.
  const zone = timeZoneAbbrev(d)
  const local = formatDateTime(d)
  return zone ? `${local} ${zone}` : local
}

/** "just now" / "6 min ago" / "3 h ago" / "2 days ago" (same scale as MRP's
 *  WMS freshness strip). null for a missing/invalid timestamp. */
export function relativeAge(iso: string | null, now: Date = new Date()): string | null {
  if (!iso) return null
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return null
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

/** 1234 → "1.2 s", 95000 → "1 min 35 s". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms} ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)} s`
  const m = Math.floor(s / 60)
  return `${m} min ${Math.round(s - m * 60)} s`
}

/** Decimal string from the API → grouped display, without float noise for
 *  the common case. Non-numeric input is shown as-is. */
export function formatNumber(value: string | number | null | undefined, maxDigits = 3): string {
  if (value == null || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return String(value)
  return n.toLocaleString('en-US', { maximumFractionDigits: maxDigits })
}

/** Today's date on the plant's clock (company time zone) as "YYYY-MM-DD".
 *  (The name is historical.) */
export function todayToronto(): string {
  return todayISO()
}

/** Last day of the month before `today` ("YYYY-MM-DD" → "YYYY-MM-DD"),
 *  computed on the digits — a date-only string never goes through Date. */
export function previousMonthEnd(today: string): string {
  const y = Number(today.slice(0, 4))
  const m = Number(today.slice(5, 7))
  const py = m === 1 ? y - 1 : y
  const pm = m === 1 ? 12 : m - 1
  const leap = (py % 4 === 0 && py % 100 !== 0) || py % 400 === 0
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][pm - 1]
  return `${py}-${String(pm).padStart(2, '0')}-${String(days).padStart(2, '0')}`
}
