// The freshness of the WMS mirror, as data and as two derivations of it.
//
// Deliberately free of any import from the api client: the *.verify.ts harness
// (see syncApi.verify.ts) runs these under plain node, and the checks it makes
// are the ones tsc cannot — that a failing sync never renders as fresh data.

export interface WmsSyncStatus {
  /** WMS_* env blank = the connection was never configured for this
   *  deployment ("feature hidden"), which is not the same as broken. */
  configured: boolean
  /** Minutes between automatic syncs. 0 = automatic sync is off; the mirror
   *  then only moves when somebody presses Refresh. */
  interval_minutes: number
  status: 'success' | 'failed' | 'empty_extract' | null
  /** Last ATTEMPT — moves even when the attempt failed. */
  last_synced_at: string | null
  /** Last attempt that actually replaced the mirror: the age of what is on
   *  screen. Read THIS for freshness, never last_synced_at (a WMS outage
   *  would otherwise keep the page looking freshly synced while the numbers
   *  quietly aged). */
  last_success_at: string | null
  row_count: number
  last_error: string | null
  next_due_at: string | null
  running: boolean
}

/** "just now" / "6 min ago" / "3 h ago" / "2 days ago".
 *
 *  Exported for its own tests. Deliberately coarse: nobody plans against
 *  seconds, and a ticking clock invites the reader to watch it instead of
 *  reading the page. Returns null for a missing timestamp so callers render
 *  "never" rather than "NaN ago".
 */
export function relativeAge(iso: string | null, now: Date = new Date()): string | null {
  if (!iso) return null
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return null
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000)
  // Clock skew between this browser and the server can put the timestamp a
  // few seconds in the future; "in -4 seconds" helps nobody.
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

/** How worried to look.
 *
 *  'stale' starts at twice the configured interval — one missed run is a
 *  hiccup, two is a pattern — with a floor of 15 minutes so a 1-minute
 *  interval does not paint the header amber every time a sync runs a little
 *  late. With automatic sync off, nothing is "late" by schedule, but data
 *  nobody has refreshed for a day is still worth flagging.
 */
export function freshness(s: WmsSyncStatus | undefined, now: Date = new Date()):
    'unknown' | 'fresh' | 'stale' | 'error' {
  if (!s) return 'unknown'
  if (!s.configured) return 'unknown'
  if (s.status === 'failed') return 'error'
  if (!s.last_success_at) return 'error'
  const ageMs = now.getTime() - new Date(s.last_success_at).getTime()
  const limitMinutes = s.interval_minutes > 0
    ? Math.max(15, s.interval_minutes * 2)
    : 24 * 60
  return ageMs > limitMinutes * 60_000 ? 'stale' : 'fresh'
}
