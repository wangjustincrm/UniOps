import { CURRENCIES, type CurrencyDef } from '@/types'

/** What epms-api seeds when no company config row exists yet (crud/config.py). */
const DEFAULT_ENABLED = ['CAD', 'USD', 'EUR', 'CNY']

// Names for the codes Portal → Admin → Currency Settings offers beyond the four
// built-in presets, used when the code was enabled there without a custom entry.
const KNOWN_NAMES: Record<string, string> = {
  GBP: 'British Pound',
  JPY: 'Japanese Yen',
  AUD: 'Australian Dollar',
  CHF: 'Swiss Franc',
}

/**
 * The currencies a transaction form may offer: exactly the ones enabled in
 * Currency Settings (PRD PO-005; ISS-0118). Filtering the four built-in
 * presets by the enabled list dropped every other enabled code — GBP and JPY
 * were enabled but never offered. Built-in presets keep their order and come
 * first; other enabled codes follow in the order they were enabled.
 *
 * `current` keeps a document's saved currency selectable after it has been
 * disabled, so an edit form does not silently show a different one.
 */
export function enabledCurrencyOptions(
  config: { enabled_currencies?: string[]; custom_currencies?: CurrencyDef[] } | undefined,
  current?: string,
): CurrencyDef[] {
  const enabled = config?.enabled_currencies ?? DEFAULT_ENABLED
  const codes = [
    ...CURRENCIES.map((c) => c.value).filter((c) => enabled.includes(c)),
    ...enabled.filter((c) => !CURRENCIES.some((p) => p.value === c)),
  ]
  if (current && !codes.includes(current)) codes.push(current)
  return [...new Set(codes)].map((code) =>
    CURRENCIES.find((c) => c.value === code)
      ?? config?.custom_currencies?.find((c) => c.value === code)
      ?? { value: code, label: KNOWN_NAMES[code] ?? code, symbol: code },
  )
}
