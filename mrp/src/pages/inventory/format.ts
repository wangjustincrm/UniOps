// Pure display formatting for inventory quantities and dates.
//
// Separate from inventoryApi.ts so `qty.verify.ts` can exercise them without
// importing the API client (and with it every alias the app resolves) — these
// are the two functions that have each produced a wrong number on screen, so
// they are the two that need to be runnable in isolation.

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
  const n = Number(value)
  if (!Number.isFinite(n)) return value
  return n.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  })
}

/** ★ Date-ONLY values (expiry, arrival, inbound) must never go through
 *  `new Date(...)`: in this timezone that parses as UTC midnight and renders
 *  as the previous day — across a year boundary, the previous YEAR. These
 *  arrive as 'YYYY-MM-DD' and are formatted by slicing, never by parsing. */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function formatDateOnly(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.slice(0, 10).split('-')
  const month = MONTHS[Number(m) - 1]
  if (!month || !y || !d) return iso
  return `${month} ${Number(d)}, ${y}`
}
