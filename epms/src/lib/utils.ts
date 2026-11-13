import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
import {
  currentMonthISO, displayTimeZone, formatDate, formatDateTime, formatTime, isoDateInZone,
  setDisplayTimeZone, timeZoneAbbrev, toLocalDate, todayISO, wallClockToISO, zonedParts,
} from '@uniops/shell'

// Dates and times have ONE implementation, in @uniops/shell (lib/date.ts):
// display format YYYY-MMM-DD / YYYY-MMM-DD HH:mm (UI-DATE-002), instants on
// the plant's time zone from Company Settings, bare dates never shifted.
export {
  currentMonthISO, displayTimeZone, formatDate, formatDateTime, formatTime, isoDateInZone,
  setDisplayTimeZone, timeZoneAbbrev, toLocalDate, todayISO, wallClockToISO, zonedParts,
}

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatCAD(amount: number): string {
  return new Intl.NumberFormat('en-CA', {
    style: 'currency',
    currency: 'CAD',
    minimumFractionDigits: 2,
  }).format(amount)
}

/** Format a monetary amount for the given currency.
 *  Handles built-in codes plus any valid ISO 4217 code (e.g. GBP, JPY, SGD).
 *  Custom codes that aren't valid ISO 4217 fall back to "CODE 1,234.56" format.
 */
/** How many decimals a unit price may show — matches the NUMERIC(15,5) columns. */
const PRICE_DP = 5

/** Unit prices carry five decimals; every other amount is money at two.
 *
 * The ERP quotes purchase prices to five decimal places, and a price rounded
 * to cents no longer multiplies out to the line total printed beside it — 319
 * of 4,944 mirrored NC lines disagreed with the ERP, by 43,256 in total, the
 * worst line by 2,000. So this shows at least two decimals (a money-looking
 * figure must not print as "10") and at most five, trimming only padding.
 *
 * Use it for UNIT PRICES ONLY. Line totals, subtotals and grand totals stay on
 * formatAmount: those are payable currency amounts, and extra digits there are
 * not more accurate, only unpayable.
 */
export function formatUnitPrice(amount: number, currency: string): string {
  // Deliberately the same construction as formatAmount below, differing in one
  // argument: maximumFractionDigits. Anything else — a hand-placed symbol, a
  // different locale — and the two would disagree about spacing or symbol in
  // adjacent columns of the same table ("CA$100.00" beside "CA$ 0.944").
  if (currency === 'RMB') {
    return '¥ ' + new Intl.NumberFormat('zh-CN', {
      minimumFractionDigits: 2, maximumFractionDigits: PRICE_DP,
    }).format(amount)
  }
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: PRICE_DP,
    }).format(amount)
  } catch {
    const num = new Intl.NumberFormat('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: PRICE_DP,
    }).format(amount)
    return `${currency} ${num}`
  }
}

export function formatAmount(amount: number, currency: string): string {
  // RMB is a colloquial code (ISO code is CNY); handle it explicitly
  if (currency === 'RMB') {
    return '¥\u00A0' + new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)
  }
  // Try Intl with the currency code directly — works for all ISO 4217 codes
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
    }).format(amount)
  } catch {
    // Fallback for non-ISO custom codes: "CODE 1,234.56"
    const num = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)
    return `${currency}\u00A0${num}`
  }
}

/** Today at the plant, YYYY-MM-DD (was the UTC date: tomorrow after 19:00/20:00). */
export function todayISODate(): string {
  return todayISO()
}

export function formatCADCompact(amount: number): string {
  const abs = Math.abs(amount)
  const sign = amount < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}CA$${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${sign}CA$${(abs / 1_000).toFixed(0)}K`
  // Not formatCAD: its en-CA locale prints "$0.00", next to "CA$11K" cards.
  return formatAmount(amount, 'CAD')
}

/** A quantity as entered: the NUMERIC(15,4) columns come back as "3.0000";
 *  show "3" (or "2.5") — trailing zeros dropped, real decimals kept. */
export function formatQty(qty: number | string | null | undefined): string {
  if (qty === null || qty === undefined || qty === '') return '—'
  const n = Number(qty)
  return Number.isFinite(n) ? String(n) : String(qty)
}

/** Human-readable file size, e.g. "512 B", "3.4 KB", "1.2 MB". */
export function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / (1024 * 1024)).toFixed(1)} MB`
}

