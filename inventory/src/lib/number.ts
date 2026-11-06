// Decimal strings from inventory-api ("12345.678900") formatted WITHOUT going
// through a JS float: the API sends Decimals as strings precisely so the
// browser does not round them, and Number() would quietly lose digits on
// large amounts. Rounding is half-up on the decimal digits themselves.
//
// formatNumber() in lib/time.ts goes through Number(); it is fine for counts
// and the Settings tables, this module is for quantities and money.

const PLAIN = /^(-)?(\d*)(?:\.(\d*))?$/

function group(intDigits: string): string {
  return intDigits.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/** "1234567.89012" → "1,234,567.89" (maxFrac 2). minFrac pads with zeros —
 *  use 2/2 for money. Non-plain input (e.g. exponent notation) falls back to
 *  Number formatting; null/empty → "—". */
export function formatDecimal(
  value: string | number | null | undefined,
  maxFrac = 3,
  minFrac = 0,
): string {
  if (value == null || value === '') return '—'
  const raw = typeof value === 'number' ? String(value) : value.trim()
  const m = PLAIN.exec(raw)
  if (!m || (m[2] === '' && (m[3] ?? '') === '')) {
    const n = Number(raw)
    if (!Number.isFinite(n)) return String(value)
    return n.toLocaleString('en-US', { minimumFractionDigits: minFrac, maximumFractionDigits: maxFrac })
  }
  const negative = m[1] === '-'
  const intPart = m[2] || '0'
  const frac = m[3] ?? ''

  let scaled = BigInt(intPart + frac.slice(0, maxFrac).padEnd(maxFrac, '0'))
  if (frac.length > maxFrac && frac[maxFrac] >= '5') scaled += 1n
  const digits = scaled.toString().padStart(maxFrac + 1, '0')
  const whole = digits.slice(0, digits.length - maxFrac)
  let fraction = maxFrac > 0 ? digits.slice(digits.length - maxFrac) : ''
  // Trim trailing zeros, but keep at least minFrac digits.
  while (fraction.length > minFrac && fraction.endsWith('0')) fraction = fraction.slice(0, -1)

  const isZero = scaled === 0n
  return `${negative && !isZero ? '-' : ''}${group(whole)}${fraction ? `.${fraction}` : ''}`
}

/** Quantities: up to 3 decimals. */
export const formatQty = (v: string | null | undefined) => formatDecimal(v, 3)

/** Money: always 2 decimals. */
export const formatMoney = (v: string | null | undefined) => formatDecimal(v, 2, 2)

/** Unit prices: 2 to 4 decimals. */
export const formatUnitPrice = (v: string | null | undefined) => formatDecimal(v, 4, 2)

/** True when the decimal string is negative (for colouring). */
export function isNegative(v: string | null | undefined): boolean {
  return !!v && v.trim().startsWith('-') && /[1-9]/.test(v)
}

/** True when the decimal string is zero (or empty). */
export function isZero(v: string | null | undefined): boolean {
  return !v || !/[1-9]/.test(v)
}

function toScaled(v: string, frac: number): bigint | null {
  const m = PLAIN.exec(v.trim())
  if (!m || (m[2] === '' && (m[3] ?? '') === '')) return null
  return BigInt((m[2] || '0') + (m[3] ?? '').padEnd(frac, '0').slice(0, frac))
}

/** |value| > tolerance, compared on the decimal digits (no float). Used to
 *  decide whether a residual is "unexplained" the same way the API does. */
export function absExceeds(value: string | null | undefined, tolerance: string): boolean {
  if (value == null || value === '') return false
  const frac = Math.max(
    (value.split('.')[1] ?? '').length,
    (tolerance.split('.')[1] ?? '').length,
  )
  const a = toScaled(value.replace(/^-/, ''), frac)
  const b = toScaled(tolerance.replace(/^-/, ''), frac)
  if (a === null || b === null) return Math.abs(Number(value)) > Math.abs(Number(tolerance))
  return a > b
}

/** Exact sum of two plain decimal strings (no float). */
export function addDecimal(a: string, b: string): string {
  const fa = (a.split('.')[1] ?? '').length
  const fb = (b.split('.')[1] ?? '').length
  const f = Math.max(fa, fb)
  const scale = (v: string) => {
    const neg = v.trim().startsWith('-')
    const [i, d = ''] = v.trim().replace(/^-/, '').split('.')
    const n = BigInt((i || '0') + d.padEnd(f, '0'))
    return neg ? -n : n
  }
  const s = scale(a) + scale(b)
  const neg = s < 0n
  const digits = (neg ? -s : s).toString().padStart(f + 1, '0')
  const whole = digits.slice(0, digits.length - f)
  const frac = f > 0 ? digits.slice(digits.length - f) : ''
  return `${neg ? '-' : ''}${whole}${frac ? `.${frac}` : ''}`
}
