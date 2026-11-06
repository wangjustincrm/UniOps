/**
 * Vendor payment terms.
 *
 * Stored as a short code: `net<N>` for "due N days after the invoice date",
 * or one of the two non-day terms `cod` / `prepayment`. The presets are only
 * the common choices — any `net<N>` in 1..MAX_NET_DAYS is valid (vendors on
 * Net 14 / Net 21 / Net 45 exist), and finance-api's AP cash-flow forecast
 * reads the day count straight out of the code. epms-api and mdm-api enforce
 * the same pattern, so keep the three in step.
 */

export type PaymentTerms = string

export const MAX_NET_DAYS = 365

export const PRESET_PAYMENT_TERMS: { value: PaymentTerms; label: string }[] = [
  { value: 'net15', label: 'Net 15' },
  { value: 'net30', label: 'Net 30' },
  { value: 'net60', label: 'Net 60' },
  { value: 'net90', label: 'Net 90' },
  { value: 'cod', label: 'COD' },
  { value: 'prepayment', label: 'Prepayment' },
]

const NET_RE = /^net(\d{1,3})$/

/** Day count of a `net<N>` code; null for cod/prepayment/anything else. */
export function netDays(terms: string | null | undefined): number | null {
  const m = NET_RE.exec((terms ?? '').trim().toLowerCase())
  if (!m) return null
  const n = Number(m[1])
  return n >= 1 && n <= MAX_NET_DAYS ? n : null
}

export function isPresetTerms(terms: string): boolean {
  return PRESET_PAYMENT_TERMS.some((p) => p.value === terms)
}

/** `net21` → "Net 21", `cod` → "COD"; unknown codes are shown as-is. */
export function formatPaymentTerms(terms: string | null | undefined): string {
  if (!terms) return '—'
  const preset = PRESET_PAYMENT_TERMS.find((p) => p.value === terms)
  if (preset) return preset.label
  const n = netDays(terms)
  return n != null ? `Net ${n}` : terms
}

/**
 * Turn what the user typed into the custom-days box into a code, or an error
 * message. Whole days only — "Net 21.5" is not a term anyone writes.
 */
export function customDaysToTerms(raw: string): { terms: PaymentTerms } | { error: string } {
  const s = raw.trim()
  if (!/^\d+$/.test(s)) return { error: 'Enter a whole number of days' }
  const n = Number(s)
  if (n < 1 || n > MAX_NET_DAYS) return { error: `Must be 1–${MAX_NET_DAYS} days` }
  return { terms: `net${n}` }
}
