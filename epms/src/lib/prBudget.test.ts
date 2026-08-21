import { describe, it, expect } from 'vitest'
import { requiresBudgetAccount, budgetAccountError } from './prBudget'

describe('requiresBudgetAccount', () => {
  it('exempts Type 1 — the Create PR form hides the Budget Account block for it', () => {
    expect(requiresBudgetAccount(1)).toBe(false)
  })

  it('requires a budget account for every other procurement type', () => {
    for (const type of [2, 3, 4, 5, 6]) {
      expect(requiresBudgetAccount(type)).toBe(true)
    }
  })

  it('treats "no type picked yet" as not requiring one', () => {
    expect(requiresBudgetAccount(null)).toBe(false)
  })
})

describe('budgetAccountError', () => {
  it('accepts a Type 2 PR with both a cost center and an L2 account', () => {
    expect(budgetAccountError(2, 'cc-uuid', '6100-01')).toBeNull()
  })

  it('rejects a Type 2 PR with no budget code — the case that reached production', () => {
    expect(budgetAccountError(2, 'cc-uuid', '')).toBe(
      'Select a budget account (cost center, category and sub-account)',
    )
  })

  it('rejects a Type 2 PR with a budget code but no cost center', () => {
    expect(budgetAccountError(2, undefined, '6100-01')).toBe(
      'Select a budget account (cost center, category and sub-account)',
    )
  })

  it('accepts a Type 1 PR with nothing selected', () => {
    expect(budgetAccountError(1, undefined, '')).toBeNull()
  })
})
