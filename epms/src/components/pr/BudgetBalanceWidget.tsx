import { cn, formatCAD } from '@/lib/utils'
import { TrendingUp, TrendingDown, Info } from 'lucide-react'

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
}

export function BudgetBalanceWidget({ data, thisAmount = 0 }: BudgetBalanceWidgetProps) {
  const available = data.annualBudget - data.committed - data.actualSpent
  const afterThis = available - thisAmount
  const isOverBudget = afterThis < 0

  const StatusIcon = isOverBudget ? TrendingDown : TrendingUp
  const statusColor = isOverBudget ? 'text-danger-600' : 'text-success-600'

  return (
    <div className="rounded-lg border border-neutral-200 bg-white overflow-hidden">
      <div className="flex items-center gap-2 border-b border-neutral-100 bg-primary-50 px-4 py-2.5">
        <Info className="h-3.5 w-3.5 text-primary-600" />
        <div className="min-w-0">
          <p className="text-xs font-semibold text-primary-700 truncate">{data.accountCode}</p>
          <p className="text-[10px] text-primary-600 truncate">{data.accountName}</p>
        </div>
      </div>

      <div className="px-4 py-3 space-y-1.5 text-sm">
        <div className="flex justify-between">
          <span className="text-neutral-500 text-xs">Annual Budget</span>
          <span className="amount text-xs text-neutral-700">{formatCAD(data.annualBudget)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-neutral-500 text-xs">Committed</span>
          <span className="amount text-xs text-neutral-700">{formatCAD(data.committed)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-neutral-500 text-xs">Actual Spent</span>
          <span className="amount text-xs text-neutral-700">{formatCAD(data.actualSpent)}</span>
        </div>

        <div className="border-t border-neutral-100 pt-1.5 flex justify-between">
          <span className="text-xs font-medium text-neutral-700">Available Balance</span>
          <span className={cn('amount text-xs font-semibold', available < 0 ? 'text-danger-600' : 'text-success-600')}>
            {formatCAD(available)}
          </span>
        </div>

        {thisAmount > 0 && (
          <div className="border-t border-neutral-100 pt-1.5">
            <div className="flex justify-between items-center">
              <span className="text-xs text-neutral-500">After this PR</span>
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
  overage: number
  pct: number
}

export function OverBudgetWarning({ accountCode, overage, pct }: OverBudgetWarningProps) {
  return (
    <div className="rounded-md border border-warning-500 bg-warning-50 p-3">
      <div className="flex gap-2">
        <span className="text-warning-500 mt-0.5 shrink-0">⚠</span>
        <div className="text-sm">
          <p className="font-medium text-warning-700">
            This PR will exceed the budget for {accountCode}
          </p>
          <p className="text-warning-600 mt-0.5">
            by {formatCAD(overage)} ({pct.toFixed(1)}% over). Please provide justification.
          </p>
        </div>
      </div>
    </div>
  )
}
