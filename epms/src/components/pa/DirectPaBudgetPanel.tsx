import { useBalance, useBudgetOverview } from '@/hooks/useBudget'
import { BudgetBalanceWidget } from '@/components/pr/BudgetBalanceWidget'
import { todayISO } from '@/lib/utils'

/** The plant's calendar year (company time zone), read when used. */
const currentFiscalYear = () => Number(todayISO().slice(0, 4))

/** Budget of the cost center × budget account a Direct PA is charged to, for
 *  reference only (ISS-0147): it never blocks the PA or adds an approval step.
 *  Same /balance figures as the New PR panel. "After this payment" subtracts
 *  the pre-tax amount, the basis budgets and NC actuals use; it is left out
 *  for a non-CAD PA (budgets are CAD, nothing is converted) and once the PA is
 *  paid (its spend is then already in the actuals). */
export function DirectPaBudgetPanel({
  costCenterId, budgetCode, amount, currency, showAfter = true,
}: {
  costCenterId: string | null | undefined
  budgetCode: string | null | undefined
  amount: number
  currency: string
  showAfter?: boolean
}) {
  const { data: balance, isLoading } = useBalance(
    costCenterId && budgetCode
      ? { cost_center_id: costCenterId, fiscal_year: currentFiscalYear(), account_code: budgetCode }
      : null,
  )
  const { data: overview } = useBudgetOverview()
  if (!costCenterId || !budgetCode) return null
  const accountName = overview?.accounts?.find((a) => a.code === budgetCode)?.name ?? ''
  const afterApplies = showAfter && currency === 'CAD'

  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide">Budget (FY{currentFiscalYear()})</h3>
      {isLoading ? (
        <p className="text-xs text-neutral-400">Loading budget…</p>
      ) : balance ? (
        <BudgetBalanceWidget
          data={{
            accountCode: budgetCode,
            accountName,
            annualBudget: Number(balance.annual_budget ?? 0),
            committed: Number(balance.committed ?? 0),
            actualSpent: Number(balance.actual_spent ?? 0),
          }}
          thisAmount={afterApplies ? amount : 0}
          thisLabel="After this payment"
        />
      ) : (
        <p className="text-xs text-neutral-400">No budget figures for this cost center and account.</p>
      )}
      <p className="text-[11px] text-neutral-400">
        For reference only — it does not block the PA or add an approval step.
        {showAfter && currency !== 'CAD' && ` The PA is in ${currency}; budgets are in CAD, so "after this payment" is not shown.`}
      </p>
    </div>
  )
}
