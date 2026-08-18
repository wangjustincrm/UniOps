// Guard: stock quantities must never be rounded for display.
//
// Reported 2026-08-18: the Inventory screen showed a lot of infant formula as
// "3" where the warehouse holds 2.8 kg. The formatter defaulted to zero
// decimal places, so every fractional quantity in the system was being
// rounded on its way to the screen — 7% wrong on that lot, and silently.
//
// Rounding a stock figure to make a column tidy is falsifying it, and nothing
// about the mistake is visible: "3" looks like a perfectly good number. tsc
// cannot see it either, which is why this is a runtime check.
//
// Run with the other *.verify.ts checks (see weekColumns.verify.ts).
/// <reference types="node" />
import { formatDateOnly, qty } from './format'

let failures = 0

function check(label: string, got: unknown, want: unknown): void {
  const ok = got === want
  if (!ok) failures++
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`)
}

console.log('qty(): fractional quantities survive to the screen')
// The reported row, exactly.
check('2.8000 kg of formula is not "3"', qty('2.8000'), '2.8')
check('a fraction under one is kept', qty('0.5'), '0.5')
check('four decimals are kept — the column is Numeric(18,4)', qty('2.8005'), '2.8005')
check('trailing zeros are dropped', qty('1000.0000'), '1,000')
check('thousands are still grouped', qty('1688123.0000'), '1,688,123')
check('a real mixed value', qty('997.9300'), '997.93')
check('zero is zero, not a dash', qty('0'), '0')

console.log('qty(): absence stays distinguishable from zero')
check('null renders as a dash', qty(null), '—')
check('undefined renders as a dash', qty(undefined), '—')
check('something unparseable is passed through, not turned into NaN',
      qty('not a number'), 'not a number')

console.log('formatDateOnly(): date-only values never go through a timezone')
// In UTC-4 a naive `new Date('2026-01-01')` renders as Dec 31 of the previous
// YEAR. These are the values that exposed it.
check('new year does not slip back a year', formatDateOnly('2026-01-01'), 'Jan 1, 2026')
check('new year eve stays put', formatDateOnly('2025-12-31'), 'Dec 31, 2025')
check('a plain date', formatDateOnly('2027-11-27'), 'Nov 27, 2027')
check('null renders as a dash', formatDateOnly(null), '—')

// Self-check: a guard that cannot fail is not a guard.
console.log('self-check: the checker actually detects a wrong answer')
const before = failures
check('(deliberately wrong)', qty('2.8'), '3')
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
