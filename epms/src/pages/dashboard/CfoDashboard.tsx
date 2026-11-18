import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { formatAmount } from '@/lib/utils'
import { useConfig } from '@/hooks/useConfig'
import { cn } from '@/lib/utils'
import { SortTh, useSortableRows, TBody, THead, Table, Td, Tr } from '@uniops/shell'

export default function CfoDashboard() {
  const { data } = useDashboard()
  const { data: config } = useConfig()
  const yellowThresholdPct = config?.budget_admin_config?.yellow_threshold_pct ?? 80
  const redThresholdPct = config?.budget_admin_config?.red_threshold_pct ?? 100

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const budget = data?.budget_overview
  const paOverview = data?.pa_overview
  const { rows: groupRows, sort, onSort } = useSortableRows(budget?.groups, {
    l1_name: (r) => r.l1_name,
    annual_budget: (r) => Number(r.annual_budget),
    committed: (r) => Number(r.committed),
    actual_spent: (r) => Number(r.actual_spent),
    available: (r) => Number(r.available),
    utilisation_pct: (r) => Number(r.utilisation_pct),
  })

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-fg">CFO Dashboard</h1>

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
          <div className="rounded-xl bg-surface shadow-sm p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-fg">Budget by Group</h2>
              <Link to="/budget" className="text-xs text-primary-600 hover:underline">Full Dashboard →</Link>
            </div>
            <div className="overflow-x-auto">
              <Table density="compact">
                <THead>
                  <Tr className="text-left">
                    <SortTh label="Group" sortKey="l1_name" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                    <SortTh label="Budget" sortKey="annual_budget" sort={sort} onSort={onSort} align="right" className="pb-2 font-medium text-fg-muted" />
                    <SortTh label="Committed" sortKey="committed" sort={sort} onSort={onSort} align="right" className="pb-2 font-medium text-fg-muted" />
                    <SortTh label="Spent" sortKey="actual_spent" sort={sort} onSort={onSort} align="right" className="pb-2 font-medium text-fg-muted" />
                    <SortTh label="Available" sortKey="available" sort={sort} onSort={onSort} align="right" className="pb-2 font-medium text-fg-muted" />
                    <SortTh label="Utilisation" sortKey="utilisation_pct" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted w-24" />
                  </Tr>
                </THead>
                <TBody>
                  {groupRows.map((row) => {
                    const pct = row.utilisation_pct
                    const barColor = pct >= redThresholdPct ? 'bg-danger-solid' : pct >= yellowThresholdPct ? 'bg-warning-solid' : 'bg-success-solid'
                    const pctColor = pct >= redThresholdPct ? 'text-danger-fg' : pct >= yellowThresholdPct ? 'text-warning-fg' : 'text-success-fg'
                    const available = Number(row.available)
                    return (
                      <Tr key={row.l1_code} className="hover:bg-surface-subtle">
                        <Td className="pr-3 font-medium text-fg">{row.l1_name}</Td>
                        <Td align="right" className="pr-3 font-mono text-fg-secondary">{formatAmount(Number(row.annual_budget), 'CAD')}</Td>
                        <Td align="right" className="pr-3 font-mono text-fg-secondary">{formatAmount(Number(row.committed), 'CAD')}</Td>
                        <Td align="right" className="pr-3 font-mono text-fg-secondary">{formatAmount(Number(row.actual_spent), 'CAD')}</Td>
                        <Td className={cn('py-2.5 pr-3 text-right font-mono', available < 0 ? 'text-danger-fg' : 'text-fg-secondary')}>
                          {formatAmount(available, 'CAD')}
                        </Td>
                        <Td>
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-strong">
                              <div className={cn('h-full rounded-full transition-all', barColor)} style={{ width: `${Math.min(pct, 100)}%` }} />
                            </div>
                            <span className={cn('w-8 text-right text-xs font-semibold', pctColor)}>{pct}%</span>
                          </div>
                        </Td>
                      </Tr>
                    )
                  })}
                </TBody>
              </Table>
            </div>
          </div>
        </div>

        {/* PA summary + links */}
        <div className="lg:col-span-4 flex flex-col gap-4">
          {/* PA summary card */}
          <div className="rounded-xl bg-surface shadow-sm p-5">
            <h2 className="mb-4 text-base font-semibold text-fg">Payment Applications</h2>
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-fg-muted">Total PAs</span>
                <span className="font-semibold text-fg">{paOverview?.total_count ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-fg-muted">Pending</span>
                <span className="font-semibold text-warning-fg">{paOverview?.pending_count ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-fg-muted">Pending Value</span>
                <span className="font-mono font-semibold text-fg">
                  {paOverview ? formatAmount(Number(paOverview.pending_value), 'CAD') : '—'}
                </span>
              </div>
              <div className="mt-1 border-t border-line-subtle pt-2 flex items-center justify-between text-sm">
                <span className="text-fg-muted">Processed Value</span>
                <span className="font-mono font-semibold text-success-fg">
                  {paOverview ? formatAmount(Number(paOverview.processed_value), 'CAD') : '—'}
                </span>
              </div>
            </div>
            <Link to="/pa" className="mt-4 block text-xs text-primary-600 hover:underline">View all PAs →</Link>
          </div>

          {/* Budget dashboard link */}
          <div className="rounded-xl bg-surface shadow-sm p-5">
            <h2 className="mb-3 text-base font-semibold text-fg">Budget Management</h2>
            <p className="text-sm text-fg-muted">View detailed budget breakdown, configure thresholds, and manage accounts.</p>
            <Link to="/budget" className="mt-4 flex items-center justify-between rounded-lg bg-primary-600 px-4 py-3 text-sm font-semibold text-white hover:bg-primary-700 transition-colors">
              <span>Full Budget Dashboard</span><span>→</span>
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
