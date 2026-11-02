import { describe, expect, it } from 'vitest'
import { enabledCurrencyOptions } from './currencies'

const codes = (opts: { value: string }[]) => opts.map((o) => o.value)

describe('enabledCurrencyOptions (ISS-0118)', () => {
  it('offers every enabled currency, not only the built-in presets', () => {
    const opts = enabledCurrencyOptions({ enabled_currencies: ['CAD', 'USD', 'EUR', 'GBP', 'CNY', 'JPY'] })
    expect(codes(opts)).toEqual(['CAD', 'USD', 'EUR', 'CNY', 'GBP', 'JPY'])
    expect(opts.find((o) => o.value === 'GBP')?.label).toBe('British Pound')
  })

  it('leaves out what is not enabled', () => {
    expect(codes(enabledCurrencyOptions({ enabled_currencies: ['CAD', 'GBP'] }))).toEqual(['CAD', 'GBP'])
  })

  it('names a custom currency from its settings entry', () => {
    const opts = enabledCurrencyOptions({
      enabled_currencies: ['CAD', 'HKD'],
      custom_currencies: [{ value: 'HKD', label: 'Hongkong Dollar', symbol: 'HK$' }],
    })
    expect(opts[1]).toEqual({ value: 'HKD', label: 'Hongkong Dollar', symbol: 'HK$' })
  })

  it("keeps a document's saved currency selectable after it was disabled", () => {
    expect(codes(enabledCurrencyOptions({ enabled_currencies: ['CAD'] }, 'USD'))).toEqual(['CAD', 'USD'])
  })

  it('falls back to the server default while the config is loading', () => {
    expect(codes(enabledCurrencyOptions(undefined))).toEqual(['CAD', 'USD', 'EUR', 'CNY'])
  })
})
