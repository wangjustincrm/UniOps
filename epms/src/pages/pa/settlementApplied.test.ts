import { describe, expect, it } from 'vitest'
import { defaultPrepaymentApplied, isSettlementOverpaid } from './settlementApplied'

describe('defaultPrepaymentApplied', () => {
  it('applies the whole prepaid total when the final value is larger', () => {
    expect(defaultPrepaymentApplied(100, 450)).toBe(100)
  })

  it('applies the prepaid total on an exact match', () => {
    expect(defaultPrepaymentApplied(113, 113)).toBe(113)
  })

  it('caps at the final value when prepaid more (ISS-0237: 226 prepaid, 113 invoiced)', () => {
    expect(defaultPrepaymentApplied(226, 113)).toBe(113)
    expect(defaultPrepaymentApplied(90.4, 67.8)).toBe(67.8)
  })

  it('shows the prepaid total while the final value is not known yet', () => {
    expect(defaultPrepaymentApplied(226, 0)).toBe(226)
  })
})

describe('isSettlementOverpaid', () => {
  it('is true only when prepaid exceeds the final value', () => {
    expect(isSettlementOverpaid(226, 113)).toBe(true)
    expect(isSettlementOverpaid(113, 113)).toBe(false)
    expect(isSettlementOverpaid(100, 450)).toBe(false)
    expect(isSettlementOverpaid(226, 0)).toBe(false)
  })
})
