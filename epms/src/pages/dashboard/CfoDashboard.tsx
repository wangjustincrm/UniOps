import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { formatAmount } from '@/lib/utils'
import { useConfig } from '@/hooks/useConfig'
import { cn } from '@/lib/utils'

export default function CfoDashboard() {
  const { data } = useDashboard()
  const { data: config } = useConfig()
  const yellowThresholdPct = config?.budget_admin_config?.yellow_threshold_pct ?? 80
  const redThresholdPct = config?.budget_admin_config?.red_threshold_pct ?? 100

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const budget = data?.budget_overview
  const paOverview = data?.pa_overview
  const groupRows = budget?.groups ?? []

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-neutral-900">CFO Dashboard</h1>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Total Budget (FY)" value={kpi('Total Budget (FY)')?.value ?? '—'} subtitle="Annual budget across all accounts" />
        <StatCard title="Total Committed" value={kpi('Total Committed')?.value ?? '—'} subtitle="Open POs and obligations" />
        <StatCard title="Total Spent" value={kpi('Total Spent')?.value ?? '—'} subtitle="Actual expenditure YTD" />
        <StatCard
          title="Budget Utilisation"
          value={kpi('Budget Utilisation')?.value ?? '—'}
          subtitle="Committed + spent vs budget"
          alert={kpi('Budget Utilisation')?.alert}
        />
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Budget group breakdown */}
        <div className="lg:col-span-8">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-neutral-900">Budget by Group</h2>
              <Link to="/budget" className="text-xs text-primary-600 hover:underline">Full Dashboard →</Link>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-neutral-100 text-left">
                    <th className="pb-2 font-medium text-neutral-500">Group</th>
                    <th className="pb-2 text-right font-medium text-neutral-500">Budget</th>
                    <th className="pb-2 text-right font-medium text-neutral-500">Committed</th>
                    <th className="pb-2 text-right font-medium text-neutral-500">Spent</th>
                    <th className="pb-2 text-right font-medium text-neutral-500">Available</th>
                    <th className="pb-2 font-medium text-neutral-500 w-24">Utilisation</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-50">
                  {groupRows.map((row) => {
                    const pct = row.utilisation_pct
                    const barColor = pct >= redThresholdPct ? 'bg-danger-600' : pct >= yellowThresholdPct ? 'bg-warning-500' : 'bg-success-600'
                    const pctColor = pct >= redThresholdPct ? 'text-danger-600' : pct >= yellowThresholdPct ? 'text-warning-600' : 'text-success-600'
                    const available = Number(row.available)
                    return (
                      <tr key={row.l1_code} className="hover:bg-neutral-50">
                        <td className="py-2.5 pr-3 font-medium text-neutral-800">{row.l1_name}</td>
                        <td className="py-2.5 pr-3 text-right font-mono text-neutral-700">{formatAmount(Number(row.annual_budget), 'CAD')}</td>
                        <td className="py-2.5 pr-3 text-right font-mono text-neutral-700">{formatAmount(Number(row.committed), 'CAD')}</td>
                        <td className="py-2.5 pr-3 text-right font-mono text-neutral-700">{formatAmount(Number(row.actual_spent), 'CAD')}</td>
                        <td className={cn('py-2.5 pr-3 text-right font-mono', available < 0 ? 'text-danger-600' : 'text-neutral-700')}>
                          {formatAmount(available, 'CAD')}
                        </td>
                        <td className="py-2.5">
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-neutral-200">
                              <div className={cn('h-full rounded-full transition-all', barColor)} style={{ width: `${Math.min(pct, 100)}%` }} />
                            </div>
                            <span className={cn('w-8 text-right text-xs font-semibold', pctColor)}>{pct}%</span>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* PA summary + links */}
        <div className="lg:col-span-4 flex flex-col gap-4">
          {/* PA summary card */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <h2 className="mb-4 text-base font-semibold text-neutral-900">Payment Applications</h2>
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-neutral-500">Total PAs</span>
                <span className="font-semibold text-neutral-900">{paOverview?.total_count ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-neutral-500">Pending</span>
                <span className="font-semibold text-warning-600">{paOverview?.pending_count ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-neutral-500">Pending Value</span>
                <span className="font-mono font-semibold text-neutral-900">
                  {paOverview ? formatAmount(Number(paOverview.pending_value), 'CAD') : '—'}
                </span>
              </div>
              <div className="mt-1 border-t border-neutral-100 pt-2 flex items-center justify-between text-sm">
                <span className="text-neutral-500">Processed Value</span>
                <span className="font-mono font-semibold text-success-700">
                  {paOverview ? formatAmount(Number(paOverview.processed_value), 'CAD') : '—'}
                </span>
              </div>
            </div>
            <Link to="/pa" className="mt-4 block text-xs text-primary-600 hover:underline">View all PAs →</Link>
          </div>

          {/* Budget dashboard link */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <h2 className="mb-3 text-base font-semibold text-neutral-900">Budget Management</h2>
            <p className="text-sm text-neutral-500">View detailed budget breakdown, configure thresholds, and manage accounts.</p>
            <Link to="/budget" className="mt-4 flex items-center justify-between rounded-lg bg-primary-600 px-4 py-3 text-sm font-semibold text-white hover:bg-primary-700 transition-colors">
              <span>Full Budget Dashboard</span><span>→</span>
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
