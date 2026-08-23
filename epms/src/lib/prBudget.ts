/**
 * Budget-account requirement for a PR, shared by the Create and Edit forms.
 *
 * Type 1 procurement carries no budget, so the forms hide the Budget Account
 * block for it. For every other type BOTH the cost center and the L2 account
 * are required: the backend's compute_budget_check short-circuits to
 * over_budget=false when either is missing, so an unbudgeted PR would bypass
 * the whole budget check (a `hard_block` Budget Config included).
 *
 * Mirrors the server-side guard in epms-api `pr_action`, which is the real
 * gate — this one only keeps the user out of a 409.
 */

export const BUDGET_ACCOUNT_REQUIRED_MESSAGE =
  'Select a budget account (cost center, category and sub-account)'

export function requiresBudgetAccount(type: number | null | undefined): boolean {
  return type != null && type !== 1
}

export function budgetAccountError(
  type: number | null | undefined,
  costCenterId: string | null | undefined,
  budgetCode: string | null | undefined,
): string | null {
  if (!requiresBudgetAccount(type)) return null
  if (!costCenterId || !budgetCode) return BUDGET_ACCOUNT_REQUIRED_MESSAGE
  return null
}
