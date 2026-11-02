import { describe, it, expect } from 'vitest'
import { formatBytes, formatCADCompact, formatQty } from './utils'
import { overBudgetFigures } from './prBudget'

// ISS-0189 display fixes.
describe('overBudgetFigures', () => {
  it('annual budget 0, already 600 spent, PR 250: this PR is over by 250, balance after −850, no %', () => {
    const f = overBudgetFigures(250, -600, 0)
    expect(f.thisPrOver).toBe(250)
    expect(f.afterThis).toBe(-850)
    expect(f.pctOverAnnual).toBeNull()
  })

  it('partly covered: only the part past what is left counts as over; % is of the annual budget', () => {
    const f = overBudgetFigures(300, 100, 1000)
    expect(f.thisPrOver).toBe(200)
    expect(f.afterThis).toBe(-200)
    expect(f.pctOverAnnual).toBeCloseTo(20)
  })

  it('never yields a negative %', () => {
    const f = overBudgetFigures(250, -600, 400)
    expect(f.pctOverAnnual).toBeGreaterThan(0)
  })
})

describe('formatQty', () => {
  it('drops the NUMERIC(15,4) padding but keeps real decimals', () => {
    expect(formatQty('3.0000')).toBe('3')
    expect(formatQty('2.5000')).toBe('2.5')
    expect(formatQty('0.1250')).toBe('0.125')
    expect(formatQty(4)).toBe('4')
  })
  it('shows a dash for no value', () => {
    expect(formatQty(null)).toBe('—')
    expect(formatQty(undefined)).toBe('—')
  })
})

describe('formatCADCompact', () => {
  it('small amounts carry the same CA$ as the compact ones', () => {
    expect(formatCADCompact(0)).toBe('CA$0.00')
    expect(formatCADCompact(12_000)).toBe('CA$12K')
  })
})

describe('formatBytes', () => {
  it('a file under 50 KB reads in KB, not "0.0 MB"', () => {
    expect(formatBytes(40 * 1024)).toBe('40.0 KB')
  })
})
