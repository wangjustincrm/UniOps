import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { BudgetOverview } from '@/components/dashboard/BudgetOverview'
import { useDashboard } from '@/hooks/useDashboard'
import { formatAmount, formatDate } from '@/lib/utils'

const PA_STATUS_STYLES: Record<string, string> = {
  draft:      'bg-neutral-100 text-neutral-600',
  submitted:  'bg-warning-100 text-warning-700',
  in_review:  'bg-blue-100 text-blue-700',
  approved:   'bg-success-100 text-success-700',
  processed:  'bg-success-100 text-success-700',
  cancelled:  'bg-neutral-100 text-neutral-500',
}

function PaStatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${PA_STATUS_STYLES[status] ?? 'bg-neutral-100 text-neutral-600'}`}>
      {status.replace('_', ' ')}
    </span>
  )
}

export default function FinanceManagerDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const recentPas = data?.pa_in_review ?? []
  const pasPendingCount = Number(kpi('PAs Pending Approval')?.value ?? 0)

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-neutral-900">Finance Manager Dashboard</h1>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          title="PAs Pending Approval"
          value={kpi('PAs Pending Approval')?.value ?? '—'}
          subtitle="Submitted or in review"
          alert={kpi('PAs Pending Approval')?.alert}
        />
        <StatCard
          title="Over-Budget Accounts"
          value={kpi('Over-Budget Accounts')?.value ?? '—'}
          subtitle="At or above threshold"
          alert={kpi('Over-Budget Accounts')?.alert}
        />
        <StatCard title="Open POs Value" value={kpi('Open POs Value')?.value ?? '—'} subtitle="Approved + issued POs" />
        <StatCard
          title="Pending Invoices"
          value={kpi('Pending Invoices')?.value ?? '—'}
          subtitle="Unmatched or exception"
          alert={kpi('Pending Invoices')?.alert}
        />
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* PAs pending approval */}
        <div className="lg:col-span-8">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-neutral-900">
                Payment Applications Pending
                {pasPendingCount > 0 && (
                  <span className="ml-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-warning-500 px-1 text-[10px] font-bold text-white">
                    {pasPendingCount}
                  </span>
                )}
              </h2>
              <Link to="/pa" className="text-xs text-primary-600 hover:underline">View all →</Link>
            </div>

            {recentPas.length === 0 ? (
              <p className="py-8 text-center text-sm text-neutral-400">No PAs pending approval.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-neutral-100 text-left">
                      <th className="pb-2 font-medium text-neutral-500">PA #</th>
                      <th className="pb-2 font-medium text-neutral-500">Vendor</th>
                      <th className="pb-2 text-right font-medium text-neutral-500">Amount</th>
                      <th className="pb-2 font-medium text-neutral-500">Status</th>
                      <th className="pb-2 font-medium text-neutral-500">Created</th>
                      <th className="pb-2 font-medium text-neutral-500"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-neutral-50">
                    {recentPas.map((pa) => (
                      <tr key={pa.id} className="hover:bg-neutral-50">
                        <td className="py-2 pr-3">
                          <Link to={`/pa/${pa.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                            {pa.pa_number}
                          </Link>
                        </td>
                        <td className="py-2 pr-3 max-w-[150px] truncate text-neutral-700">{pa.vendor_name}</td>
                        <td className="py-2 pr-3 text-right font-mono text-neutral-900">
                          {formatAmount(Number(pa.payment_amount), pa.currency)}
                        </td>
                        <td className="py-2 pr-3"><PaStatusBadge status={pa.status} /></td>
                        <td className="py-2 pr-3 text-neutral-500">{formatDate(pa.created_at)}</td>
                        <td className="py-2">
                          <Link to={`/pa/${pa.id}`} className="text-xs text-primary-600 hover:underline whitespace-nowrap">
                            Review →
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Budget overview */}
        <div className="lg:col-span-4">
          <BudgetOverview groups={data?.budget_overview?.groups} />
        </div>
      </div>
    </div>
  )
}
