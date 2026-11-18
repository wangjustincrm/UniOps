import { cn, formatCAD } from '@/lib/utils'
import { overBudgetFigures } from '@/lib/prBudget'
import { TrendingUp, TrendingDown, Info, AlertTriangle } from 'lucide-react'

interface BudgetData {
  accountCode: string
  accountName: string
  annualBudget: number
  committed: number
  actualSpent: number
}

interface BudgetBalanceWidgetProps {
  data: BudgetData
  thisAmount?: number
  /** Label of the "after" row — the PR page's default; a Direct PA says "After this payment". */
  thisLabel?: string
}

export function BudgetBalanceWidget({ data, thisAmount = 0, thisLabel = 'After this PR' }: BudgetBalanceWidgetProps) {
  const available = data.annualBudget - data.committed - data.actualSpent
  const afterThis = available - thisAmount
  const isOverBudget = afterThis < 0

  const StatusIcon = isOverBudget ? TrendingDown : TrendingUp
  const statusColor = isOverBudget ? 'text-danger-fg' : 'text-success-fg'

  return (
    <div className="rounded-lg border border-line bg-surface overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line-subtle bg-primary-50 px-4 py-2.5">
        <Info className="h-3.5 w-3.5 text-primary-600" />
        <div className="min-w-0">
          <p className="text-xs font-semibold text-primary-700 truncate">{data.accountCode}</p>
          <p className="text-2xs text-primary-600 truncate">{data.accountName}</p>
        </div>
      </div>

      <div className="px-4 py-3 space-y-1.5 text-sm">
        <div className="flex justify-between">
          <span className="text-fg-muted text-xs">Annual Budget</span>
          <span className="amount text-xs text-fg-secondary">{formatCAD(data.annualBudget)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-fg-muted text-xs">Committed</span>
          <span className="amount text-xs text-fg-secondary">{formatCAD(data.committed)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-fg-muted text-xs">Actual Spent</span>
          <span className="amount text-xs text-fg-secondary">{formatCAD(data.actualSpent)}</span>
        </div>

        <div className="border-t border-line-subtle pt-1.5 flex justify-between">
          <span className="text-xs font-medium text-fg-secondary">Available Balance</span>
          <span className={cn('amount text-xs font-semibold', available < 0 ? 'text-danger-fg' : 'text-success-fg')}>
            {formatCAD(available)}
          </span>
        </div>

        {thisAmount > 0 && (
          <div className="border-t border-line-subtle pt-1.5">
            <div className="flex justify-between items-center">
              <span className="text-xs text-fg-muted">{thisLabel}</span>
              <div className="flex items-center gap-1">
                <StatusIcon className={cn('h-3 w-3', statusColor)} />
                <span className={cn('amount text-xs font-semibold', statusColor)}>
                  {formatCAD(afterThis)}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

interface OverBudgetWarningProps {
  accountCode: string
  /** This PR's amount. */
  thisAmount: number
  /** Balance available before this PR (may already be negative). */
  available: number
  annualBudget: number
}

export function OverBudgetWarning({ accountCode, thisAmount, available, annualBudget }: OverBudgetWarningProps) {
  const { thisPrOver, afterThis, pctOverAnnual: pctOver } = overBudgetFigures(thisAmount, available, annualBudget)
  return (
    <div className="rounded-md border border-warning-solid bg-warning-soft p-3">
      <div className="flex gap-2">
        <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warning-fg" />
        <div className="text-sm">
          <p className="font-medium text-warning-fg">
            This PR will exceed the budget for {accountCode}
          </p>
          <p className="text-warning-fg mt-0.5">
            This PR is over by {formatCAD(thisPrOver)}. Balance after this PR: {formatCAD(afterThis)}
            {pctOver !== null && <> ({pctOver.toFixed(1)}% over the annual budget)</>}
            . Please provide justification.
          </p>
        </div>
      </div>
    </div>
  )
}
