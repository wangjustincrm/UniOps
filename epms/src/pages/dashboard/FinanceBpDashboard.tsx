import { Link } from 'react-router-dom'
import { StatCard } from '@/components/dashboard/StatCard'
import { useDashboard } from '@/hooks/useDashboard'
import { displayTimeZone, formatAmount, formatDate, todayISO } from '@/lib/utils'
import { SortTh, useSortableRows, TBody, THead, Table, Td, Th, Tr, Badge } from '@uniops/shell'

function PaTypeBadge({ type }: { type: string }) {
  return type === 'prepayment' ? (
    <Badge variant="blue">
      Prepayment
    </Badge>
  ) : (
    <Badge>
      Regular
    </Badge>
  )
}

export default function FinanceBpDashboard() {
  const { data } = useDashboard()

  const kpi = (title: string) => data?.kpis.find((k) => k.title === title)
  // Recent-N widget, loaded whole: sorting the rows here is exact.
  const { rows: inReview, sort, onSort } = useSortableRows(data?.pa_in_review, {
    pa_number: (p) => p.pa_number,
    vendor_name: (p) => p.vendor_name,
    po_number: (p) => p.po_number,
    pa_type: (p) => (p.pa_type === 'prepayment' ? 'Prepayment' : 'Regular'),
    payment_amount: (p) => Number(p.payment_amount),
    created_at: (p) => p.created_at,
  })
  const inReviewCount = inReview.length

  const now = new Date()
  const todayStr = formatDate(todayISO())

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-fg">Finance BP Dashboard</h1>

      {/* Stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          title="PAs In Review"
          value={kpi('PAs In Review')?.value ?? '—'}
          subtitle="Awaiting your review"
          alert={kpi('PAs In Review')?.alert}
        />
        <StatCard title="Approved Today" value={kpi('Approved Today')?.value ?? '—'} subtitle={todayStr} />
        <StatCard title="Total PA Value Pending" value={kpi('Total PA Value Pending')?.value ?? '—'} subtitle="In review" />
        <StatCard
          title="Processed This Month"
          value={kpi('Processed This Month')?.value ?? '—'}
          subtitle={now.toLocaleString('en-CA', { month: 'long', year: 'numeric', timeZone: displayTimeZone() })}
        />
      </div>

      {/* PAs in review table */}
      <div className="rounded-xl bg-surface shadow-sm p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">
            Payment Applications In Review
            {inReviewCount > 0 && (
              <span className="ml-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-warning-solid px-1 text-2xs font-semibold text-white">
                {inReviewCount}
              </span>
            )}
          </h2>
          <Link to="/pa" className="text-xs text-primary-600 hover:underline">View all PAs →</Link>
        </div>

        {inReview.length === 0 ? (
          <div className="py-12 text-center">
            <p className="text-sm font-medium text-fg-muted">No PAs currently in review</p>
            <p className="mt-1 text-xs text-fg-muted">You're all caught up.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table density="compact">
              <THead>
                <Tr className="text-left">
                  <SortTh label="PA #" sortKey="pa_number" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="PO #" sortKey="po_number" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Type" sortKey="pa_type" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Amount" sortKey="payment_amount" sort={sort} onSort={onSort} align="right" className="pb-2 font-medium text-fg-muted" />
                  <SortTh label="Created" sortKey="created_at" sort={sort} onSort={onSort} className="pb-2 font-medium text-fg-muted" />
                  <Th></Th>
                </Tr>
              </THead>
              <TBody>
                {inReview.map((pa) => (
                  <Tr key={pa.id} className="hover:bg-surface-subtle">
                    <Td className="pr-3">
                      <Link to={`/pa/${pa.id}`} className="font-mono text-xs text-primary-600 hover:underline">
                        {pa.pa_number}
                      </Link>
                    </Td>
                    <Td className="pr-3 max-w-[150px] truncate text-fg-secondary">{pa.vendor_name}</Td>
                    <Td className="pr-3">
                      {/* A payment may settle several POs. The row has space for
                          one, so it names the primary and counts the rest —
                          showing the primary alone reads as the whole payment. */}
                      <span className="font-mono text-xs text-fg-secondary">
                        {pa.po_number ?? '—'}
                        {(pa.po_numbers?.length ?? 0) > 1 && (
                          <span className="ml-1 text-fg-muted">+{(pa.po_numbers?.length ?? 1) - 1}</span>
                        )}
                      </span>
                    </Td>
                    <Td className="pr-3"><PaTypeBadge type={pa.pa_type} /></Td>
                    <Td align="right" className="pr-3 font-mono text-fg">
                      {formatAmount(Number(pa.payment_amount), pa.currency)}
                    </Td>
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
  )
}
