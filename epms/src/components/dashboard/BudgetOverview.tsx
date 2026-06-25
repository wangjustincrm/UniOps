import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import type { BudgetGroupRow } from '@/services/dashboard'

interface BudgetOverviewProps {
  groups?: BudgetGroupRow[]
  yellow?: number
  red?: number
}

function BudgetBar({ dept, pct, yellow, red }: { dept: string; pct: number; yellow: number; red: number }) {
  const color     = pct >= red ? 'bg-danger-600'  : pct >= yellow ? 'bg-warning-500' : 'bg-success-600'
  const textColor = pct >= red ? 'text-danger-600' : pct >= yellow ? 'text-warning-600' : 'text-success-600'
  const icon      = pct >= red ? '🔴' : pct >= yellow ? '🟡' : '🟢'

  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-medium text-neutral-700">{dept}</span>
        <span className={cn('font-semibold', textColor)}>
          {pct}% {icon}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-200">
        <div
          className={cn('h-full rounded-full transition-all', color)}
          style={{ width: `${Math.min(pct, 100)}%` }}
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
    </div>
  )
}

export function BudgetOverview({ groups, yellow: propYellow, red: propRed }: BudgetOverviewProps = {}) {
  const yellow = propYellow ?? 70
  const red = propRed ?? 90

  const items = (groups ?? []).map((g) => ({ dept: g.l1_name, pct: g.utilisation_pct }))

  return (
    <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
      <h2 className="mb-4 text-base font-semibold text-neutral-900">Budget Overview</h2>
      {items.length === 0 ? (
        <p className="py-4 text-center text-sm text-neutral-400">No budget data available</p>
      ) : (
        items.map((item) => (
          <BudgetBar key={item.dept} dept={item.dept} pct={item.pct} yellow={yellow} red={red} />
        ))
      )}
      <Link to="/budget" className="mt-2 block text-xs text-primary-600 hover:underline">
        Full Dashboard →
      </Link>
    </div>
  )
}
