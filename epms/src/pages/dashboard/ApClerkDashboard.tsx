import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { displayTimeZone, formatAmount, formatDate } from '@/lib/utils'
import { SortTh, useSortableRows, TBody, THead, Table, Td, Tr, Badge, type BadgeTone } from '@uniops/shell'

const INV_STATUS_TONE: Record<string, BadgeTone> = {
  unmatched: 'warning',
  matched:   'success',
  exception: 'danger',
  approved:  'blue',
  paid:      'neutral',
}

function InvoiceStatusBadge({ status }: { status: string }) {
  return (
    <Badge variant={INV_STATUS_TONE[status] ?? 'neutral'}>
      {status}
    </Badge>
  )
}

export default function ApClerkDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  // Recent-N widget, loaded whole: sorting the rows here is exact.
  const { rows: recentInvoices, sort, onSort } = useSortableRows(data?.recent_invoices, {
    internal_ref: (i) => i.internal_ref,
    vendor_name: (i) => i.vendor_name,
    po_number: (i) => i.po_number,
    total_amount: (i) => Number(i.total_amount),
    status: (i) => i.status,
    created_at: (i) => i.created_at,
  })

  const now = new Date()

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-fg">AP Clerk Dashboard</h1>

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
          subtitle={now.toLocaleString('en-CA', { month: 'long', year: 'numeric', timeZone: displayTimeZone() })}
        />
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Recent invoices table */}
        <div className="lg:col-span-8">
          <div className="rounded-xl bg-surface shadow-sm p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-fg">Recent Invoices</h2>
              <Link to="/invoices" className="text-xs text-primary-600 hover:underline">View all →</Link>
            </div>
            {recentInvoices.length === 0 ? (
              <p className="py-8 text-center text-sm text-fg-muted">No invoices yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <Table density="compact">
                  <THead>
                    <Tr className="text-left">
                      <SortTh label="Invoice #" sortKey="internal_ref" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="PO #" sortKey="po_number" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Amount" sortKey="total_amount" sort={sort} onSort={onSort} align="right" className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Created" sortKey="created_at" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                    </Tr>
                  </THead>
                  <TBody>
                    {recentInvoices.map((inv) => (
                      <Tr key={inv.id} className="hover:bg-surface-subtle">
                        <Td className="pr-3">
                          <Link to={`/invoices/${inv.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                            {inv.internal_ref}
                          </Link>
                        </Td>
                        <Td className="pr-3 max-w-[140px] truncate text-fg-secondary">{inv.vendor_name}</Td>
                        <Td className="pr-3">
                          {inv.po_number ? (
                            <span className="font-mono text-xs text-fg-secondary">{inv.po_number}</span>
                          ) : (
                            <span className="text-xs text-fg-muted">—</span>
                          )}
                        </Td>
                        <Td align="right" className="pr-3 font-mono text-fg">
                          {formatAmount(Number(inv.total_amount), inv.currency)}
                        </Td>
                        <Td className="pr-3"><InvoiceStatusBadge status={inv.status} /></Td>
                        <Td className="text-fg-muted">{formatDate(inv.created_at)}</Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </div>
            )}
          </div>
        </div>

        {/* Quick links */}
        <div className="lg:col-span-4">
          <div className="rounded-xl bg-surface shadow-sm p-5">
            <h2 className="mb-4 text-base font-semibold text-fg">Quick Actions</h2>
            <div className="flex flex-col gap-3">
              <Link to="/invoices" className="flex items-center justify-between rounded-lg bg-primary-600 px-4 py-3 text-sm font-semibold text-white hover:bg-primary-700 transition-colors">
                <span>Upload Invoice</span><span>→</span>
              </Link>
              <Link to="/invoices" className="flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-3 text-sm font-medium text-fg-secondary hover:bg-surface-subtle transition-colors">
                <span>View Invoices</span><span className="text-fg-muted">→</span>
              </Link>
              <Link to="/pa" className="flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-3 text-sm font-medium text-fg-secondary hover:bg-surface-subtle transition-colors">
                <span>Payment Applications</span><span className="text-fg-muted">→</span>
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
