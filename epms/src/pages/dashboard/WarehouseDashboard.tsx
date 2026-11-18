import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { displayTimeZone, formatDate } from '@/lib/utils'
import { SortTh, useSortableRows, TBody, THead, Table, Td, Th, Tr, Badge, type BadgeTone } from '@uniops/shell'

const GR_STATUS_TONE: Record<string, BadgeTone> = {
  pending_ack:        'warning',
  collection_pending: 'blue',
  collected:          'success',
  confirmed:          'success',
  discrepancy:        'danger',
  cancelled:          'neutral',
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
    <Badge variant={GR_STATUS_TONE[status] ?? 'neutral'}>
      {GR_STATUS_LABEL[status] ?? status}
    </Badge>
  )
}

export default function WarehouseDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  // Recent-N widget, loaded whole: sorting the rows here is exact.
  const { rows: recentGrs, sort, onSort } = useSortableRows(data?.recent_grs, {
    number: (g) => g.number,
    gr_type: (g) => (g.gr_type === 'physical' ? 'Physical' : 'Service'),
    po_number: (g) => g.po_number,
    vendor_name: (g) => g.vendor_name,
    status: (g) => GR_STATUS_LABEL[g.status] ?? g.status,
    created_at: (g) => g.created_at,
  })

  const now = new Date()

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-fg">Warehouse Dashboard</h1>
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
        {/* "Collection Overdue" (Collection Config → Finance Manager alert) is
            counted by the server, only while collection is required. */}
        <StatCard
          title="Collection Pending"
          value={kpi('Collection Pending')?.value ?? '—'}
          subtitle={kpi('Collection Pending')?.subtitle ?? 'Ready for requester collection'}
          alert={kpi('Collection Pending')?.alert}
        />
        <StatCard
          title="Discrepancies"
          value={kpi('Discrepancies')?.value ?? '—'}
          subtitle="Unresolved issues"
          alert={kpi('Discrepancies')?.alert}
        />
        <StatCard
          title="Completed This Month"
          value={kpi('Completed This Month')?.value ?? '—'}
          subtitle={now.toLocaleString('en-CA', { month: 'long', year: 'numeric', timeZone: displayTimeZone() })}
        />
      </div>

      {/* GRs table */}
      <div className="rounded-xl bg-surface shadow-sm p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">Open Goods Receipts</h2>
          <Link to="/gr" className="text-xs text-primary-600 hover:underline">View all →</Link>
        </div>

        {recentGrs.length === 0 ? (
          <div className="py-12 text-center">
            <p className="text-sm font-medium text-fg-muted">No open goods receipts</p>
            <p className="mt-1 text-xs text-fg-muted">All GRs are resolved or cancelled.</p>
            <Link to="/gr/new" className="mt-4 inline-flex items-center rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 transition-colors">
              Create New GR
            </Link>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table density="compact">
              <THead>
                <Tr className="text-left">
                  <SortTh label="GR #" sortKey="number" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Type" sortKey="gr_type" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="PO #" sortKey="po_number" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Created" sortKey="created_at" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <Th></Th>
                </Tr>
              </THead>
              <TBody>
                {recentGrs.map((gr) => (
                  <Tr key={gr.id} className="hover:bg-surface-subtle">
                    <Td className="pr-3">
                      <span className="font-mono text-xs text-fg-secondary">{gr.number}</span>
                    </Td>
                    <Td className="pr-3">
                      {gr.gr_type === 'physical' ? (
                        <Badge variant="blue">Physical</Badge>
                      ) : (
                        <Badge variant="cat4">Service</Badge>
                      )}
                    </Td>
                    <Td className="pr-3">
                      <span className="font-mono text-xs text-fg-secondary">{gr.po_number}</span>
                    </Td>
                    <Td className="pr-3 max-w-[140px] truncate text-fg-muted">{gr.vendor_name}</Td>
                    <Td className="pr-3"><GrStatusBadge status={gr.status} /></Td>
                    <Td className="pr-3 text-fg-muted">{formatDate(gr.created_at)}</Td>
                    <Td>
                      <Link to={`/gr/${gr.id}`} className="text-xs text-primary-600 hover:underline whitespace-nowrap">View →</Link>
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  )
}
