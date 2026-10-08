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

/**
 * Body for `POST /pr/budget-check`.
 *
 * Exists to be testable. The call is the authoritative over-budget verdict —
 * the Create form prefers its answer over the local subtraction, and that one
 * boolean drives the warning banner, whether an Over-Budget Justification is
 * demanded, and whether a `hard_block` Budget Config disables Submit.
 *
 * ★ It all turns on `budget_code`. The server's compute_budget_check opens with
 * `if not budget_code ... return False, None`, and BudgetCheckRequest defaults
 * the field to None — so a body that omits it does not fail, it answers "not
 * over budget", every time. That is exactly what shipped: the query key listed
 * the account, the body did not, and the endpoint had been replying false to
 * the Create page for as long as it existed. Nothing looked broken, because a
 * PR that is within budget and a PR nobody checked render identically.
 *
 * Built here, and asserted on, so the next edit to this payload cannot drop the
 * field in silence again.
 */
export interface BudgetCheckArgs {
  costCenterId: string
  budgetCode: string
  amount: number
  departmentId?: string | null
  factorCombo?: Record<string, string> | null
  projectCode?: string | null
}

export function budgetCheckPayload(args: BudgetCheckArgs): Record<string, unknown> {
  return {
    cost_center_id: args.costCenterId,
    budget_code: args.budgetCode,
    department_id: args.departmentId ?? undefined,
    factor_combo: args.factorCombo ?? undefined,
    project_code: args.projectCode || undefined,
    amount: args.amount,
  }
}
