import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { formatAmount, formatDate } from '@/lib/utils'

/**
 * Payment Officer — the payment-execution half split out of AP Clerk
 * (2026-08-13). Deliberately shows no unmatched/exception invoice counts: those
 * belong to the AP review job this role is separated from. `pa_in_review` here
 * carries APPROVED PAs waiting to be paid (see build_payment_officer).
 *
 * payment_officer is an ADDITIONAL role, so this page is reached through the
 * dashboard payload's `role` field, not the JWT's primary role.
 */
function PaTypeBadge({ type }: { type: string }) {
  return type === 'prepayment' ? (
    <span className="inline-flex items-center rounded-full bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-700">
      Prepayment
    </span>
  ) : (
    <span className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-600">
      Regular
    </span>
  )
}

export default function PaymentOfficerDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  const awaiting = data?.pa_in_review ?? []
  const now = new Date()
  const thisMonth = now.toLocaleString('en-CA', { month: 'long', year: 'numeric' })

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-neutral-900">Payment Officer Dashboard</h1>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          title="PAs Awaiting Payment"
          value={kpi('PAs Awaiting Payment')?.value ?? '—'}
          subtitle="Approved, not yet paid"
          alert={kpi('PAs Awaiting Payment')?.alert}
        />
        <StatCard
          title="Value Awaiting Payment"
          value={kpi('Value Awaiting Payment')?.value ?? '—'}
          subtitle="Total approved"
        />
        <StatCard
          title="Processed This Month"
          value={kpi('Processed This Month')?.value ?? '—'}
          subtitle={thisMonth}
        />
        <StatCard
          title="Value Processed This Month"
          value={kpi('Value Processed This Month')?.value ?? '—'}
          subtitle={thisMonth}
        />
      </div>

      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-900">
            Awaiting Payment
            {awaiting.length > 0 && (
              <span className="ml-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-warning-500 px-1 text-[10px] font-bold text-white">
                {awaiting.length}
              </span>
            )}
          </h2>
          <Link to="/pa" className="text-xs text-primary-600 hover:underline">View all PAs →</Link>
        </div>

        {awaiting.length === 0 ? (
          <div className="py-12 text-center">
            <p className="text-sm font-medium text-neutral-500">Nothing waiting to be paid</p>
            <p className="mt-1 text-xs text-neutral-400">You're all caught up.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-100 text-left">
                  <th className="pb-2 font-medium text-neutral-500">PA #</th>
                  <th className="pb-2 font-medium text-neutral-500">Vendor</th>
                  <th className="pb-2 font-medium text-neutral-500">PO #</th>
                  <th className="pb-2 font-medium text-neutral-500">Type</th>
                  <th className="pb-2 text-right font-medium text-neutral-500">Amount</th>
                  <th className="pb-2 font-medium text-neutral-500">Created</th>
                  <th className="pb-2 font-medium text-neutral-500"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-50">
                {awaiting.map((pa) => (
                  <tr key={pa.id} className="hover:bg-neutral-50">
                    <td className="py-2 pr-3">
                      <Link to={`/pa/${pa.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                        {pa.pa_number}
                      </Link>
                    </td>
                    <td className="py-2 pr-3 max-w-[150px] truncate text-neutral-700">{pa.vendor_name}</td>
                    <td className="py-2 pr-3">
                      <span className="font-mono text-xs text-neutral-600">{pa.po_number}</span>
                    </td>
                    <td className="py-2 pr-3"><PaTypeBadge type={pa.pa_type} /></td>
                    <td className="py-2 pr-3 text-right font-mono text-neutral-900">
                      {formatAmount(Number(pa.payment_amount), pa.currency)}
                    </td>
                    <td className="py-2 pr-3 text-neutral-500">{formatDate(pa.created_at)}</td>
                    <td className="py-2">
                      <Link to={`/pa/${pa.id}`} className="text-xs text-primary-600 hover:underline whitespace-nowrap">
                        Process →
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
  )
}
