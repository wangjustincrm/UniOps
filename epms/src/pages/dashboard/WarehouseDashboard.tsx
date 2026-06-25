import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { formatDate } from '@/lib/utils'

const GR_STATUS_STYLES: Record<string, string> = {
  pending_ack:        'bg-warning-100 text-warning-700',
  collection_pending: 'bg-blue-100 text-blue-700',
  collected:          'bg-success-100 text-success-700',
  confirmed:          'bg-success-100 text-success-700',
  discrepancy:        'bg-danger-100 text-danger-700',
  cancelled:          'bg-neutral-100 text-neutral-500',
}

const GR_STATUS_LABEL: Record<string, string> = {
  pending_ack:        'Pending ACK',
  collection_pending: 'Collection Pending',
  collected:          'Collected',
  confirmed:          'Confirmed',
  discrepancy:        'Discrepancy',
  cancelled:          'Cancelled',
}

function GrStatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${GR_STATUS_STYLES[status] ?? 'bg-neutral-100 text-neutral-600'}`}>
      {GR_STATUS_LABEL[status] ?? status}
    </span>
  )
}

export default function WarehouseDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const recentGrs = data?.recent_grs ?? []

  const now = new Date()

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-neutral-900">Warehouse Dashboard</h1>
        <Link to="/gr/new" className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 transition-colors">
          + New GR
        </Link>
      </div>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          title="GRs Awaiting ACK"
          value={kpi('GRs Awaiting ACK')?.value ?? '—'}
          subtitle="Pending requester acknowledgement"
          alert={kpi('GRs Awaiting ACK')?.alert}
        />
        <StatCard title="Collection Pending" value={kpi('Collection Pending')?.value ?? '—'} subtitle="Ready for requester collection" />
        <StatCard
          title="Discrepancies"
          value={kpi('Discrepancies')?.value ?? '—'}
          subtitle="Unresolved issues"
          alert={kpi('Discrepancies')?.alert}
        />
        <StatCard
          title="Completed This Month"
          value={kpi('Completed This Month')?.value ?? '—'}
          subtitle={now.toLocaleString('en-CA', { month: 'long', year: 'numeric' })}
        />
      </div>

      {/* GRs table */}
      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-900">Open Goods Receipts</h2>
          <Link to="/gr" className="text-xs text-primary-600 hover:underline">View all →</Link>
        </div>

        {recentGrs.length === 0 ? (
          <div className="py-12 text-center">
            <p className="text-sm font-medium text-neutral-500">No open goods receipts</p>
            <p className="mt-1 text-xs text-neutral-400">All GRs are resolved or cancelled.</p>
            <Link to="/gr/new" className="mt-4 inline-flex items-center rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 transition-colors">
              Create New GR
            </Link>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-100 text-left">
                  <th className="pb-2 font-medium text-neutral-500">GR #</th>
                  <th className="pb-2 font-medium text-neutral-500">Type</th>
                  <th className="pb-2 font-medium text-neutral-500">PO #</th>
                  <th className="pb-2 font-medium text-neutral-500">Vendor</th>
                  <th className="pb-2 font-medium text-neutral-500">Status</th>
                  <th className="pb-2 font-medium text-neutral-500">Created</th>
                  <th className="pb-2 font-medium text-neutral-500"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-50">
                {recentGrs.map((gr) => (
                  <tr key={gr.id} className="hover:bg-neutral-50">
                    <td className="py-2 pr-3">
                      <span className="font-mono text-xs text-neutral-700">{gr.number}</span>
                    </td>
                    <td className="py-2 pr-3">
                      {gr.gr_type === 'physical' ? (
                        <span className="inline-flex items-center rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">Physical</span>
                      ) : (
                        <span className="inline-flex items-center rounded-full bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-700">Service</span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      <span className="font-mono text-xs text-neutral-600">{gr.po_number}</span>
                    </td>
                    <td className="py-2 pr-3 max-w-[140px] truncate text-neutral-500">{gr.vendor_name}</td>
                    <td className="py-2 pr-3"><GrStatusBadge status={gr.status} /></td>
                    <td className="py-2 pr-3 text-neutral-500">{formatDate(gr.created_at)}</td>
                    <td className="py-2">
                      <Link to={`/gr/${gr.id}`} className="text-xs text-primary-600 hover:underline whitespace-nowrap">View →</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
