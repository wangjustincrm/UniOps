import { describe, expect, it } from 'vitest'
import { customDaysToTerms, formatPaymentTerms, isPresetTerms, netDays } from './paymentTerms'

describe('netDays', () => {
  it('reads presets and custom day counts', () => {
    expect(netDays('net30')).toBe(30)
    expect(netDays('net21')).toBe(21)
    expect(netDays('net14')).toBe(14)
  })

  it('is null for the non-day terms and junk', () => {
    expect(netDays('cod')).toBeNull()
    expect(netDays('prepayment')).toBeNull()
    expect(netDays('net0')).toBeNull()
    expect(netDays('net400')).toBeNull()
    expect(netDays('')).toBeNull()
    expect(netDays(null)).toBeNull()
  })
})

describe('formatPaymentTerms', () => {
  it('labels presets and custom terms the same way', () => {
    expect(formatPaymentTerms('net30')).toBe('Net 30')
    expect(formatPaymentTerms('net21')).toBe('Net 21')
    expect(formatPaymentTerms('cod')).toBe('COD')
    expect(formatPaymentTerms('prepayment')).toBe('Prepayment')
  })

  it('shows an unknown code verbatim rather than hiding it', () => {
    expect(formatPaymentTerms('prepaid')).toBe('prepaid')
  })
})

describe('customDaysToTerms', () => {
  it('builds a net code from a day count', () => {
    expect(customDaysToTerms('21')).toEqual({ terms: 'net21' })
    expect(customDaysToTerms(' 14 ')).toEqual({ terms: 'net14' })
  })

  it('rejects blanks, fractions, zero and out-of-range', () => {
    for (const bad of ['', 'abc', '21.5', '0', '366', '-5']) {
      expect(customDaysToTerms(bad)).toHaveProperty('error')
    }
  })

  it('a custom value equal to a preset is still a preset', () => {
    const r = customDaysToTerms('30')
    expect(r).toEqual({ terms: 'net30' })
    expect(isPresetTerms('net30')).toBe(true)
    expect(isPresetTerms('net21')).toBe(false)
  })
})
