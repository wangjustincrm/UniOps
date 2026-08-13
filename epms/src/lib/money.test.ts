import { describe, expect, it } from 'vitest'
import { centsEqual } from './money'

describe('centsEqual', () => {
  it('treats exactly equal values as equal', () => {
    expect(centsEqual(100.25, 100.25)).toBe(true)
  })

  it('absorbs sub-cent float noise from summing decimal strings', () => {
    // 19.99 + 0.01 + 80.00 does not land exactly on 100 in IEEE754
    const summed = 19.99 + 0.01 + 80.0
    expect(summed === 100).toBe(false)
    expect(centsEqual(summed, 100)).toBe(true)
  })

  it('reports a genuine one-cent difference as unequal', () => {
    expect(centsEqual(100.0, 100.01)).toBe(false)
  })

  it('is symmetric', () => {
    expect(centsEqual(100.01, 100.0)).toBe(centsEqual(100.0, 100.01))
  })
})
