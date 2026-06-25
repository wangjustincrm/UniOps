import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { formatAmount, formatDate } from '@/lib/utils'

const PO_STATUS_STYLES: Record<string, string> = {
  draft:      'bg-neutral-100 text-neutral-600',
  submitted:  'bg-warning-100 text-warning-700',
  in_review:  'bg-blue-100 text-blue-700',
  approved:   'bg-success-100 text-success-700',
  issued:     'bg-primary-100 text-primary-700',
  closed:     'bg-neutral-100 text-neutral-500',
  cancelled:  'bg-danger-100 text-danger-700',
}

function PoStatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${PO_STATUS_STYLES[status] ?? 'bg-neutral-100 text-neutral-600'}`}>
      {status.replace('_', ' ')}
    </span>
  )
}

export default function ProcurementDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const recentPos = data?.recent_pos ?? []

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-neutral-900">Procurement Dashboard</h1>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Open POs" value={kpi('Open POs')?.value ?? '—'} subtitle="Active purchase orders" />
        <StatCard
          title="POs Pending Approval"
          value={kpi('POs Pending Approval')?.value ?? '—'}
          subtitle="Submitted or in review"
          alert={kpi('POs Pending Approval')?.alert}
        />
        <StatCard title="Total Committed (FY)" value={kpi('Total Committed (FY)')?.value ?? '—'} subtitle="Non-cancelled POs" />
        <StatCard title="Open GRs" value={kpi('Open GRs')?.value ?? '—'} subtitle="Awaiting ACK or collection" />
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Recent POs table */}
        <div className="lg:col-span-7">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-neutral-900">Recent Purchase Orders</h2>
              <Link to="/po" className="text-xs text-primary-600 hover:underline">View all →</Link>
            </div>
            {recentPos.length === 0 ? (
              <p className="py-8 text-center text-sm text-neutral-400">No purchase orders yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-neutral-100 text-left">
                      <th className="pb-2 font-medium text-neutral-500">PO #</th>
                      <th className="pb-2 font-medium text-neutral-500">Title</th>
                      <th className="pb-2 font-medium text-neutral-500">Vendor</th>
                      <th className="pb-2 text-right font-medium text-neutral-500">Amount</th>
                      <th className="pb-2 font-medium text-neutral-500">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-neutral-50">
                    {recentPos.map((po) => (
                      <tr key={po.id} className="group hover:bg-neutral-50">
                        <td className="py-2 pr-3">
                          <Link to={`/po/${po.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                            {po.number}
                          </Link>
                        </td>
                        <td className="py-2 pr-3 text-neutral-700 max-w-[140px] truncate">{po.title}</td>
                        <td className="py-2 pr-3 text-neutral-500 max-w-[120px] truncate">{po.vendor_name}</td>
                        <td className="py-2 pr-3 text-right font-mono text-neutral-900">
                          {formatAmount(Number(po.total), po.currency)}
                        </td>
                        <td className="py-2"><PoStatusBadge status={po.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Quick links */}
        <div className="lg:col-span-5">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <h2 className="mb-4 text-base font-semibold text-neutral-900">Quick Actions</h2>
            <div className="flex flex-col gap-3">
              <Link to="/po/new" className="flex items-center justify-between rounded-lg bg-primary-600 px-4 py-3 text-sm font-semibold text-white hover:bg-primary-700 transition-colors">
                <span>New Purchase Order</span><span>→</span>
              </Link>
              <Link to="/po" className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors">
                <span>View All POs</span><span className="text-neutral-400">→</span>
              </Link>
              <Link to="/gr" className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors">
                <span>Goods Receipts</span><span className="text-neutral-400">→</span>
              </Link>
              <Link to="/invoices" className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors">
                <span>Invoices</span><span className="text-neutral-400">→</span>
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
