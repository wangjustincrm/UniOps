import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { formatAmount, formatDate } from '@/lib/utils'

const INV_STATUS_STYLES: Record<string, string> = {
  unmatched: 'bg-warning-100 text-warning-700',
  matched:   'bg-success-100 text-success-700',
  exception: 'bg-danger-100 text-danger-700',
  approved:  'bg-blue-100 text-blue-700',
  paid:      'bg-neutral-100 text-neutral-500',
}

function InvoiceStatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${INV_STATUS_STYLES[status] ?? 'bg-neutral-100 text-neutral-600'}`}>
      {status}
    </span>
  )
}

export default function ApClerkDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const recentInvoices = data?.recent_invoices ?? []

  const now = new Date()

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-neutral-900">AP Clerk Dashboard</h1>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          title="Invoices to Match"
          value={kpi('Invoices to Match')?.value ?? '—'}
          subtitle="Unmatched invoices"
          alert={kpi('Invoices to Match')?.alert}
        />
        <StatCard
          title="Exceptions"
          value={kpi('Exceptions')?.value ?? '—'}
          subtitle="Require resolution"
          alert={kpi('Exceptions')?.alert}
        />
        <StatCard title="PAs Pending" value={kpi('PAs Pending')?.value ?? '—'} subtitle="Submitted or in review" />
        <StatCard
          title="Processed This Month"
          value={kpi('Processed This Month')?.value ?? '—'}
          subtitle={now.toLocaleString('en-CA', { month: 'long', year: 'numeric' })}
        />
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Recent invoices table */}
        <div className="lg:col-span-8">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-neutral-900">Recent Invoices</h2>
              <Link to="/invoices" className="text-xs text-primary-600 hover:underline">View all →</Link>
            </div>
            {recentInvoices.length === 0 ? (
              <p className="py-8 text-center text-sm text-neutral-400">No invoices yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-neutral-100 text-left">
                      <th className="pb-2 font-medium text-neutral-500">Invoice #</th>
                      <th className="pb-2 font-medium text-neutral-500">Vendor</th>
                      <th className="pb-2 font-medium text-neutral-500">PO #</th>
                      <th className="pb-2 text-right font-medium text-neutral-500">Amount</th>
                      <th className="pb-2 font-medium text-neutral-500">Status</th>
                      <th className="pb-2 font-medium text-neutral-500">Created</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-neutral-50">
                    {recentInvoices.map((inv) => (
                      <tr key={inv.id} className="hover:bg-neutral-50">
                        <td className="py-2 pr-3">
                          <Link to={`/invoices/${inv.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                            {inv.internal_ref}
                          </Link>
                        </td>
                        <td className="py-2 pr-3 max-w-[140px] truncate text-neutral-700">{inv.vendor_name}</td>
                        <td className="py-2 pr-3">
                          {inv.po_number ? (
                            <span className="font-mono text-xs text-neutral-600">{inv.po_number}</span>
                          ) : (
                            <span className="text-xs text-neutral-400">—</span>
                          )}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono text-neutral-900">
                          {formatAmount(Number(inv.total_amount), inv.currency)}
                        </td>
                        <td className="py-2 pr-3"><InvoiceStatusBadge status={inv.status} /></td>
                        <td className="py-2 text-neutral-500">{formatDate(inv.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Quick links */}
        <div className="lg:col-span-4">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
            <h2 className="mb-4 text-base font-semibold text-neutral-900">Quick Actions</h2>
            <div className="flex flex-col gap-3">
              <Link to="/invoices" className="flex items-center justify-between rounded-lg bg-primary-600 px-4 py-3 text-sm font-semibold text-white hover:bg-primary-700 transition-colors">
                <span>Upload Invoice</span><span>→</span>
              </Link>
              <Link to="/invoices" className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors">
                <span>View Invoices</span><span className="text-neutral-400">→</span>
              </Link>
              <Link to="/pa" className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors">
                <span>Payment Applications</span><span className="text-neutral-400">→</span>
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
