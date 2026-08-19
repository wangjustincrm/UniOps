// Guard: the freshness strip must never make stale data look current.
//
// This is the whole point of the feature. The page previously showed only
// `as of <today>` — the shelf-life reference date — so a mirror last synced
// three weeks earlier still printed today's date next to every quantity.
// The two ways to reintroduce that bug are (a) measuring age from the last
// ATTEMPT instead of the last successful replace, so a WMS outage keeps the
// header green, and (b) never going amber at all. Both are checked here.
//
// tsc cannot see either mistake — both are type-correct — which is why this
// is a runtime check. Run with the other *.verify.ts files.
/// <reference types="node" />
import { freshness, relativeAge, type WmsSyncStatus } from './freshness'

let failures = 0

function check(label: string, got: unknown, want: unknown): void {
  const ok = got === want
  if (!ok) failures++
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`)
}

const NOW = new Date('2026-08-18T12:00:00Z')
const ago = (minutes: number) => new Date(NOW.getTime() - minutes * 60_000).toISOString()

function status(over: Partial<WmsSyncStatus> = {}): WmsSyncStatus {
  return {
    configured: true, interval_minutes: 5, status: 'success',
    last_synced_at: ago(3), last_success_at: ago(3), row_count: 3452,
    last_error: null, next_due_at: null, running: false, ...over,
  }
}

console.log('relativeAge(): coarse, and never negative')
check('under a minute reads as just now', relativeAge(ago(0.5), NOW), 'just now')
check('minutes', relativeAge(ago(6), NOW), '6 min ago')
check('hours', relativeAge(ago(200), NOW), '3 h ago')
check('days', relativeAge(ago(60 * 24 * 2), NOW), '2 days ago')
check('one day is singular', relativeAge(ago(60 * 24), NOW), '1 day ago')
check('never synced has no age', relativeAge(null, NOW), null)
check('clock skew does not print a negative age',
      relativeAge(new Date(NOW.getTime() + 4000).toISOString(), NOW), 'just now')

console.log('\nfreshness(): a failing sync cannot look fresh')
check('a recent successful snapshot is fresh', freshness(status(), NOW), 'fresh')
check('twice the interval, floored at 15 min, is still fresh',
      freshness(status({ last_success_at: ago(14) }), NOW), 'fresh')
check('older than that is stale',
      freshness(status({ last_success_at: ago(31), interval_minutes: 15 }), NOW), 'stale')
// The regression this file exists for: WMS down, attempts still ticking.
check('a fresh ATTEMPT over an old snapshot is an error, not freshness',
      freshness(status({ status: 'failed', last_synced_at: ago(1),
                         last_success_at: ago(600) }), NOW), 'error')
check('never successfully synced is an error',
      freshness(status({ last_success_at: null, status: null }), NOW), 'error')
check('with automatic sync off, a day-old snapshot is still fresh',
      freshness(status({ interval_minutes: 0, last_success_at: ago(60 * 20) }), NOW), 'fresh')
check('with automatic sync off, older than a day is stale',
      freshness(status({ interval_minutes: 0, last_success_at: ago(60 * 30) }), NOW), 'stale')
check('an unconfigured WMS is not a fault to report',
      freshness(status({ configured: false }), NOW), 'unknown')
check('no status yet says nothing', freshness(undefined, NOW), 'unknown')

// Self-check: a file of assertions that cannot fail proves nothing.
const before = failures
check('(deliberately wrong)', freshness(status(), NOW), 'stale')
if (failures !== before + 1) {
  console.log('FAIL the self-check did not register — this file proves nothing')
  process.exit(1)
}
failures = before

if (failures > 0) {
  console.log(`\n${failures} check(s) failed`)
  process.exit(1)
}
console.log('\nall checks passed')
