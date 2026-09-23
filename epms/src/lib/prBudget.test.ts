import { describe, it, expect } from 'vitest'
import { requiresBudgetAccount, budgetAccountError, budgetCheckPayload } from './prBudget'

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

describe('budgetCheckPayload', () => {
  const args = {
    costCenterId: 'cc-uuid',
    budgetCode: 'CRM00301',
    amount: 1200,
    departmentId: 'dept-uuid',
  }

  it('sends budget_code — without it the endpoint always answers "not over budget"', () => {
    // The regression this file exists to stop. compute_budget_check opens with
    // `if not budget_code: return False, None`, so an omitted field is not an
    // error, it is a false negative — and the Create form trusts that answer
    // over its own arithmetic.
    expect(budgetCheckPayload(args).budget_code).toBe('CRM00301')
  })

  it('sends the cost center and amount, the other two the verdict needs', () => {
    const body = budgetCheckPayload(args)
    expect(body.cost_center_id).toBe('cc-uuid')
    expect(body.amount).toBe(1200)
  })

  it('carries the three fields the server accepts for form parity', () => {
    const body = budgetCheckPayload({
      ...args, factorCombo: { size: 'L' }, projectCode: 'P-1',
    })
    expect(body.department_id).toBe('dept-uuid')
    expect(body.factor_combo).toEqual({ size: 'L' })
    expect(body.project_code).toBe('P-1')
  })

  it('omits the optional three rather than sending null', () => {
    // BudgetCheckRequest types them `| None`, so null would validate — but an
    // empty project_code is "no project", not a project named "". Keep the key
    // absent so the server applies its own default.
    const body = budgetCheckPayload({ ...args, departmentId: null, projectCode: '' })
    expect(body.department_id).toBeUndefined()
    expect(body.project_code).toBeUndefined()
    expect(body.factor_combo).toBeUndefined()
  })

  it('names every key the server reads, and no others', () => {
    // BudgetCheckRequest's full field list. A key this payload invents is
    // dropped by pydantic in silence, which is how a typo'd field name becomes
    // another permanent false negative.
    expect(Object.keys(budgetCheckPayload(args)).sort()).toEqual([
      'amount', 'budget_code', 'cost_center_id',
      'department_id', 'factor_combo', 'project_code',
    ])
  })
})
