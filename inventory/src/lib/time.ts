// Timestamps (real instants, ISO with offset) shown on Toronto's clock — the
// plant's clock, whatever zone the reader's laptop is set to. Date-only
// strings ("YYYY-MM-DD") must NOT come through here: they have no instant,
// and new Date() would read them as UTC midnight (one day early at UTC-4).
// Show those as the string itself.

export const LOCAL_TIME_ZONE = 'America/Toronto'

/** "Oct 14, 2026, 17:05 EDT" on Toronto's clock; "—" for null/invalid. */
export function formatDateTimeToronto(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-CA', {
    timeZone: LOCAL_TIME_ZONE,
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
    timeZoneName: 'short',
  })
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
