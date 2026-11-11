// Pure display formatting for inventory quantities and dates (copied from
// MRP's Inventory page). Quantities are formatted on the decimal digits via
// lib/number.ts, never through a JS float.
import { formatDecimal, isNegative, isZero } from '@/lib/number'
import { formatDate } from '@/lib/utils'

/** > 0 on the decimal digits (no float). */
export function isPositiveQty(value: string | null | undefined): boolean {
  return !isZero(value) && !isNegative(value)
}

/** Quantities are Decimal-as-string on the wire. One place converts them, so a
 *  column cannot quietly start rendering "1000.0000" — or, as it did until
 *  2026-08-18, rendering 2.8 kg of infant formula as "3".
 *
 *  ★ Up to FOUR decimals, and no minimum. The column is Numeric(18,4), so four
 *  never loses a stored digit, and dropping the trailing zeros keeps whole
 *  quantities reading as "1,000" rather than "1,000.0000". Rounding a stock
 *  figure to make a column tidy is falsifying it: the warehouse holds 2.8, and
 *  a screen that says 3 is wrong by more than it looks — 7% of that lot.
 */
export function qty(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—'
  // On the decimal digits, not through a float (lib/number.ts).
  return formatDecimal(value, 4)
}

/** ★ Date-ONLY values (expiry, arrival, inbound) must never go through
 *  `new Date(...)` as a full ISO string: in this timezone that parses as UTC
 *  midnight and renders as the previous day — across a year boundary, the
 *  previous YEAR. Only the 'YYYY-MM-DD' part is kept and handed to the shared
 *  formatDate (which reads a bare date as a local date): "2026-NOV-11". */
export function formatDateOnly(iso: string | null | undefined): string {
  if (!iso) return '—'
  const out = formatDate(iso.slice(0, 10))
  return out === '—' ? iso : out
}
