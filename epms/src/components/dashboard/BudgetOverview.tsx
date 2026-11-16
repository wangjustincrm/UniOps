import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import type { BudgetGroupRow } from '@/services/dashboard'

interface BudgetOverviewProps {
  groups?: BudgetGroupRow[]
  yellow?: number
  red?: number
}

type Tone = 'red' | 'amber' | 'green'

/**
 * How one L1 group reads. Over budget (red) also when the group has spend but
 * no budget, or holds an over-budget account even though its total is under:
 * BUDGET.md BDASH-003 counts spend with no budget as over, and a 0% green bar
 * read as "nothing spent" (ISS-0168).
 */
export function groupStatus(
  g: Pick<BudgetGroupRow, 'annual_budget' | 'committed' | 'actual_spent' | 'utilisation_pct' | 'over_accounts'>,
  yellow: number, red: number,
): { tone: Tone; label: string; barPct: number } {
  const pct = g.utilisation_pct
  const used = Number(g.committed) + Number(g.actual_spent)
  const over = g.over_accounts ?? 0
  if (Number(g.annual_budget) <= 0 && used > 0) {
    return { tone: 'red', label: 'No budget', barPct: 100 }
  }
  if (over > 0) {
    return { tone: 'red', label: `${pct}% · ${over} over`, barPct: pct }
  }
  return { tone: pct >= red ? 'red' : pct >= yellow ? 'amber' : 'green', label: `${pct}%`, barPct: pct }
}

const TONE = {
  red:   { bar: 'bg-danger-600',  text: 'text-danger-600',  icon: '🔴' },
  amber: { bar: 'bg-warning-500', text: 'text-warning-600', icon: '🟡' },
  green: { bar: 'bg-success-600', text: 'text-success-600', icon: '🟢' },
} as const

function BudgetBar({ dept, tone, label, barPct }: { dept: string; tone: Tone; label: string; barPct: number }) {
  const t = TONE[tone]

  return (
    <div className="mb-3">
      <div className="mb-1 flex items-center justify-between gap-2 text-xs">
        <span className="font-medium text-neutral-700">{dept}</span>
        <span className={cn('shrink-0 font-semibold', t.text)}>
          {label} {t.icon}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-200">
        <div
          className={cn('h-full rounded-full transition-all', t.bar)}
          style={{ width: `${Math.min(barPct, 100)}%` }}
          role="progressbar"
          aria-valuenow={barPct}
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

  const items = (groups ?? []).map((g) => ({ dept: g.l1_name, ...groupStatus(g, yellow, red) }))

  return (
    <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
      <h2 className="mb-4 text-base font-semibold text-neutral-900">Budget Overview</h2>
      {items.length === 0 ? (
        <p className="py-4 text-center text-sm text-neutral-400">No budget data available</p>
      ) : (
        items.map((item) => (
          <BudgetBar key={item.dept} dept={item.dept} tone={item.tone} label={item.label} barPct={item.barPct} />
        ))
      )}
      <Link to="/budget" className="mt-2 block text-xs text-primary-600 hover:underline">
        Full Dashboard →
      </Link>
    </div>
  )
}
