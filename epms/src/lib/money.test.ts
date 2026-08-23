import { describe, expect, it } from 'vitest'
import { centsEqual } from './money'

describe('centsEqual', () => {
  it('treats exactly equal values as equal', () => {
    expect(centsEqual(100.25, 100.25)).toBe(true)
  })

  it('absorbs sub-cent float noise from summing decimal strings', () => {
    // Verified with node before writing: 1234.56 + 0.07 lands on
    // 1234.6299999999998818, so `===` genuinely fails here.
    // Do NOT substitute a "nicer" example without running it first —
    // this assertion originally used 19.99 + 0.01 + 80.00, which sums to
    // EXACTLY 100 in IEEE754 and therefore tested nothing.
    const summed = 1234.56 + 0.07
    expect(summed === 1234.63).toBe(false)
    expect(centsEqual(summed, 1234.63)).toBe(true)
  })

  it('reports a genuine one-cent difference as unequal', () => {
    expect(centsEqual(100.0, 100.01)).toBe(false)
  })

  it('is symmetric', () => {
    expect(centsEqual(100.01, 100.0)).toBe(centsEqual(100.0, 100.01))
  })
})
