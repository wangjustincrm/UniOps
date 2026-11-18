import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { formatDate } from '@/lib/utils'
import { formatPaymentTerms } from '@/lib/paymentTerms'
import { SortTh, useSortableRows } from '@uniops/shell'

export default function VendorManagerDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  // Recent-N widget, loaded whole: sorting the rows here is exact.
  const { rows: recentVendors, sort, onSort } = useSortableRows(data?.recent_vendors, {
    code: (v) => v.code,
    name: (v) => v.name,
    category: (v) => v.category,
    contact_name: (v) => v.contact_name,
    payment_terms: (v) => formatPaymentTerms(v.payment_terms),
    created_at: (v) => v.created_at,
    status: (v) => (v.is_active ? 'Active' : 'Inactive'),
  })

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-fg">Vendor Manager Dashboard</h1>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Total Vendors" value={kpi('Total Vendors')?.value ?? '—'} subtitle="All registered vendors" />
        <StatCard title="Active Vendors" value={kpi('Active Vendors')?.value ?? '—'} subtitle="Currently active" />
        <StatCard
          title="Inactive Vendors"
          value={kpi('Inactive Vendors')?.value ?? '—'}
          subtitle="Deactivated vendors"
          alert={kpi('Inactive Vendors')?.alert}
        />
        <StatCard title="Categories" value={kpi('Categories')?.value ?? '—'} subtitle="Vendor categories" />
      </div>

      {/* Vendor table */}
      <div className="rounded-xl bg-surface shadow-sm p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">Recent Vendors</h2>
          <Link to="/vendors" className="text-xs text-primary-600 hover:underline">Go to Vendors →</Link>
        </div>

        {recentVendors.length === 0 ? (
          <p className="py-8 text-center text-sm text-fg-muted">No vendors registered yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line-subtle text-left">
                  <SortTh label="Code" sortKey="code" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Name" sortKey="name" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Category" sortKey="category" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Contact" sortKey="contact_name" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Payment Terms" sortKey="payment_terms" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Added" sortKey="created_at" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <th className="pb-2 font-medium text-fg-muted"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-subtle">
                {recentVendors.map((vendor) => (
                  <tr key={vendor.id} className="hover:bg-surface-subtle">
                    <td className="py-2 pr-3">
                      <span className="font-mono text-xs font-semibold text-fg-secondary">{vendor.code}</span>
                    </td>
                    <td className="py-2 pr-3 max-w-[160px] truncate font-medium text-fg">{vendor.name}</td>
                    <td className="py-2 pr-3 text-fg-muted">{vendor.category}</td>
                    <td className="py-2 pr-3 text-fg-muted">{vendor.contact_name}</td>
                    <td className="py-2 pr-3 text-fg-muted">{formatPaymentTerms(vendor.payment_terms)}</td>
                    <td className="py-2 pr-3 text-fg-muted">{formatDate(vendor.created_at)}</td>
                    <td className="py-2 pr-3">
                      {vendor.is_active ? (
                        <span className="inline-flex items-center rounded-full bg-success-soft px-2 py-0.5 text-xs font-medium text-success-fg">Active</span>
                      ) : (
                        <span className="inline-flex items-center rounded-full bg-surface-sunken px-2 py-0.5 text-xs font-medium text-fg-muted">Inactive</span>
                      )}
                    </td>
                    <td className="py-2">
                      <Link to="/vendors" className="text-xs text-primary-600 hover:underline whitespace-nowrap">View Details →</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-4 border-t border-line-subtle pt-3">
          <Link to="/vendors" className="text-sm text-primary-600 hover:underline font-medium">Go to Vendors →</Link>
        </div>
      </div>
    </div>
  )
}
