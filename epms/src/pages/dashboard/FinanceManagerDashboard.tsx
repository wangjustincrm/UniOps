import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { BudgetOverview } from '@/components/dashboard/BudgetOverview'
import { useDashboard } from '@/hooks/useDashboard'
import { formatAmount, formatDate } from '@/lib/utils'
import { SortTh, useSortableRows, TBody, THead, Table, Td, Th, Tr, Badge, type BadgeTone } from '@uniops/shell'

const PA_STATUS_TONE: Record<string, BadgeTone> = {
  draft:      'neutral',
  submitted:  'warning',
  in_review:  'blue',
  approved:   'success',
  processed:  'success',
  cancelled:  'dark',
}

function PaStatusBadge({ status }: { status: string }) {
  return (
    <Badge variant={PA_STATUS_TONE[status] ?? 'neutral'}>
      {status.replace('_', ' ')}
    </Badge>
  )
}

export default function FinanceManagerDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  // Recent-N widget, loaded whole: sorting the rows here is exact.
  const { rows: recentPas, sort, onSort } = useSortableRows(data?.pa_in_review, {
    pa_number: (p) => p.pa_number,
    vendor_name: (p) => p.vendor_name,
    payment_amount: (p) => Number(p.payment_amount),
    status: (p) => p.status.replace('_', ' '),
    created_at: (p) => p.created_at,
  })
  const pasPendingCount = Number(kpi('PAs Pending Approval')?.value ?? 0)

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-fg">Finance Manager Dashboard</h1>

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
          <div className="rounded-xl bg-surface shadow-sm p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-fg">
                Payment Applications Pending
                {pasPendingCount > 0 && (
                  <span className="ml-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-warning-solid px-1 text-2xs font-semibold text-white">
                    {pasPendingCount}
                  </span>
                )}
              </h2>
              <Link to="/pa" className="text-xs text-primary-600 hover:underline">View all →</Link>
            </div>

            {recentPas.length === 0 ? (
              <p className="py-8 text-center text-sm text-fg-muted">No PAs pending approval.</p>
            ) : (
              <div className="overflow-x-auto">
                <Table density="compact">
                  <THead>
                    <Tr className="text-left">
                      <SortTh label="PA #" sortKey="pa_number" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Amount" sortKey="payment_amount" sort={sort} onSort={onSort} align="right" className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <SortTh label="Created" sortKey="created_at" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                      <Th></Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {recentPas.map((pa) => (
                      <Tr key={pa.id} className="hover:bg-surface-subtle">
                        <Td className="pr-3">
                          <Link to={`/pa/${pa.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                            {pa.pa_number}
                          </Link>
                        </Td>
                        <Td className="pr-3 max-w-[150px] truncate text-fg-secondary">{pa.vendor_name}</Td>
                        <Td align="right" className="pr-3 font-mono text-fg">
                          {formatAmount(Number(pa.payment_amount), pa.currency)}
                        </Td>
                        <Td className="pr-3"><PaStatusBadge status={pa.status} /></Td>
                        <Td className="pr-3 text-fg-muted">{formatDate(pa.created_at)}</Td>
                        <Td>
                          <Link to={`/pa/${pa.id}`} className="text-xs text-primary-600 hover:underline whitespace-nowrap">
                            Review →
                          </Link>
                        </Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
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
