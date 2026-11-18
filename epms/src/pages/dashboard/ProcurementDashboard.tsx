import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { formatAmount } from '@/lib/utils'
import { SortTh, useSortableRows, TBody, THead, Table, Td, Tr, Badge, type BadgeTone } from '@uniops/shell'

const PO_STATUS_TONE: Record<string, BadgeTone> = {
  draft:      'neutral',
  submitted:  'warning',
  in_review:  'blue',
  approved:   'success',
  issued:     'accent',
  closed:     'outline',
  cancelled:  'danger',
}

function PoStatusBadge({ status }: { status: string }) {
  return (
    <Badge variant={PO_STATUS_TONE[status] ?? 'neutral'}>
      {status.replace('_', ' ')}
    </Badge>
  )
}

export default function ProcurementDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  // Recent-N widget, loaded whole: sorting the rows here is exact.
  const { rows: recentPos, sort, onSort } = useSortableRows(data?.recent_pos, {
    number: (p) => p.number,
    title: (p) => p.title,
    vendor_name: (p) => p.vendor_name,
    total: (p) => Number(p.total),
    status: (p) => p.status.replace('_', ' '),
  })

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-fg">Procurement Dashboard</h1>

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
          <div className="rounded-xl bg-surface shadow-sm p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-fg">Recent Purchase Orders</h2>
              <Link to="/po" className="text-xs text-primary-600 hover:underline">View all →</Link>
            </div>
            {recentPos.length === 0 ? (
              <p className="py-8 text-center text-sm text-fg-muted">No purchase orders yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <Table density="compact">
                  <THead>
                    <Tr className="text-left">
                      <SortTh label="PO #" sortKey="number" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Title" sortKey="title" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Amount" sortKey="total" sort={sort} onSort={onSort} align="right" className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                    </Tr>
                  </THead>
                  <TBody>
                    {recentPos.map((po) => (
                      <Tr key={po.id} className="group hover:bg-surface-subtle">
                        <Td className="pr-3">
                          <Link to={`/po/${po.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                            {po.number}
                          </Link>
                        </Td>
                        <Td className="pr-3 text-fg-secondary max-w-[140px] truncate">{po.title}</Td>
                        <Td className="pr-3 text-fg-muted max-w-[120px] truncate">{po.vendor_name}</Td>
                        <Td align="right" className="pr-3 font-mono text-fg">
                          {formatAmount(Number(po.total), po.currency)}
                        </Td>
                        <Td><PoStatusBadge status={po.status} /></Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </div>
            )}
          </div>
        </div>

        {/* Quick links */}
        <div className="lg:col-span-5">
          <div className="rounded-xl bg-surface shadow-sm p-5">
            <h2 className="mb-4 text-base font-semibold text-fg">Quick Actions</h2>
            <div className="flex flex-col gap-3">
              <Link to="/po/new" className="flex items-center justify-between rounded-lg bg-primary-600 px-4 py-3 text-sm font-semibold text-white hover:bg-primary-700 transition-colors">
                <span>New Purchase Order</span><span>→</span>
              </Link>
              <Link to="/po" className="flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-3 text-sm font-medium text-fg-secondary hover:bg-surface-subtle transition-colors">
                <span>View All POs</span><span className="text-fg-muted">→</span>
              </Link>
              <Link to="/gr" className="flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-3 text-sm font-medium text-fg-secondary hover:bg-surface-subtle transition-colors">
                <span>Goods Receipts</span><span className="text-fg-muted">→</span>
              </Link>
              <Link to="/invoices" className="flex items-center justify-between rounded-lg border border-line bg-surface px-4 py-3 text-sm font-medium text-fg-secondary hover:bg-surface-subtle transition-colors">
                <span>Invoices</span><span className="text-fg-muted">→</span>
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
