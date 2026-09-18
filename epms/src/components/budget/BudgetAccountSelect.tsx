import { type JSX } from 'react'
import { useBudgetOverview } from '@/hooks/useBudget'

/**
 * A flat budget-account picker: one select over the whole L2 catalog, grouped
 * by L1 category.
 *
 * Why not the three-step BudgetAccountCascade used by PR and Agreements? That
 * cascade starts at Cost Center, and `purchase_orders` has no cost_center_id —
 * there is nothing on a PO to anchor step 1 to. What a PO needs is only the
 * guarantee that whatever lands in `budget_code` is a real account code, which
 * a single grouped select gives without inventing a dimension the row doesn't
 * carry.
 *
 * Both PO pages used to render a free-text <Input> here (placeholder: "e.g.
 * CRM003-01"), which is how `purchase_orders.budget_code` ended up holding
 * "CRM0090201-Machinery Spare Parts Supplies" and friends — a code glued to a
 * name, matching nothing on an equality join. The equivalent PR field was
 * changed to a catalog dropdown on 2026-07-17; this is the PO half of that.
 *
 * `value` is the account CODE (what the column stores), never the id.
 */
export function BudgetAccountSelect(props: {
  id?: string
  value: string
  onChange: (code: string) => void
  disabled?: boolean
}): JSX.Element {
  const { id, value, onChange, disabled } = props

  // isError vs empty matters: budget-api is a separate service on its own base
  // URL (:8007). When it is down this query rejects and the select renders with
  // nothing but its placeholder, which reads as "the catalog is empty" and
  // sends the operator looking in the wrong place. Same reasoning — and the
  // same message — as BudgetAccountCascade.
  const { data: budgetData, isError } = useBudgetOverview()

  const l1Groups = budgetData?.l1_groups ?? []

  // Inactive accounts stay out of the list, except the one already selected:
  // retiring an account must not silently rewrite the documents that used it.
  const known = new Set(
    l1Groups.flatMap((l1) => (l1.accounts ?? []).map((a) => a.code)),
  )

  // A value the catalog doesn't contain (a legacy "CODE-Name" string, or a code
  // from a superseded chart of accounts) still has to appear as the selected
  // option. Drop it from the list instead and the select renders blank, so the
  // next save of an otherwise unrelated edit silently clears the field — the
  // edit page would become a data-loss path for exactly the rows that need
  // attention. Show it, marked, and let the user replace it deliberately.
  const unrecognized = value && !known.has(value) ? value : null

  return (
    <div className="flex flex-col gap-1.5">
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400"
      >
        <option value="">Select budget account…</option>
        {unrecognized && (
          <optgroup label="Not in the chart of accounts">
            <option value={unrecognized}>{unrecognized} — unrecognized, please re-select</option>
          </optgroup>
        )}
        {l1Groups
          .filter((l1) => l1.is_active)
          .map((l1) => {
            const accounts = (l1.accounts ?? []).filter(
              (a) => a.is_active || a.code === value,
            )
            if (accounts.length === 0) return null
            return (
              <optgroup key={l1.id} label={`${l1.code} — ${l1.name}`}>
                {accounts.map((a) => (
                  <option key={a.id} value={a.code}>{a.code} — {a.name}</option>
                ))}
              </optgroup>
            )
          })}
      </select>
      {isError && (
        <p className="text-xs text-danger-600">
          Couldn't load the budget accounts — the budget service is unreachable. This is not
          an empty catalog; try again shortly.
        </p>
      )}
    </div>
  )
}
