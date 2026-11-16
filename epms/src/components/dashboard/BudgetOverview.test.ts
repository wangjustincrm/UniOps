/**
 * ISS-0168: a Budget Overview group with spend but no budget, or with an
 * over-budget account inside an under-budget total, read "0%, green".
 */
import { describe, it, expect } from 'vitest'
import { groupStatus } from './BudgetOverview'

const row = (b: number, spent: number, pct: number, over = 0) => ({
  annual_budget: b, committed: 0, actual_spent: spent, utilisation_pct: pct, over_accounts: over,
})

describe('groupStatus', () => {
  it('spend with no budget is over, not 0% green (CRM001 TRAVEL)', () => {
    expect(groupStatus(row(0, 244.54, 0, 2), 70, 90)).toEqual({ tone: 'red', label: 'No budget', barPct: 100 })
  })

  it('a group under budget overall but holding an over account is red', () => {
    expect(groupStatus(row(10000, 2000, 20, 1), 70, 90)).toEqual({ tone: 'red', label: '20% · 1 over', barPct: 20 })
  })

  it('no budget and no spend stays a plain 0%', () => {
    expect(groupStatus(row(0, 0, 0), 70, 90)).toEqual({ tone: 'green', label: '0%', barPct: 0 })
  })

  it('keeps the threshold colours otherwise', () => {
    expect(groupStatus(row(100, 50, 50), 70, 90).tone).toBe('green')
    expect(groupStatus(row(100, 75, 75), 70, 90).tone).toBe('amber')
    expect(groupStatus(row(100, 95, 95), 70, 90).tone).toBe('red')
  })

  it('works with an older API that does not send over_accounts', () => {
    const { over_accounts: _omit, ...old } = row(100, 50, 50)
    expect(groupStatus(old, 70, 90)).toEqual({ tone: 'green', label: '50%', barPct: 50 })
  })
})
