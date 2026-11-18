import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus, AlertCircle } from 'lucide-react'
import { Pagination, SortTh, useSort, sortParams, TBody, THead, Table, Td, Tr } from '@uniops/shell'
import { useVisits, type Visit } from '@/services/api'
import { StatusBadge, AccessAreaBadge, OverdueBadge, isVisitOverdue } from '@/components/StatusBadge'
import { displayTimeZone, formatDate, formatDateTime, localToday } from '@/lib/utils'

/** vms-api /visits ?sort= keys (app/crud/visit.py VISIT_SORTABLE). */
export type VisitSortKey = 'visitor' | 'company' | 'planned_arrival' | 'access_area' | 'status'

export default function VisitListPage({ scope }: { scope: 'today' | 'all' }) {
  const today = localToday()
  // Server-paged and server-sorted (/visits caps page_size at 100). The two
  // routes reuse this component, so the page resets when the scope changes.
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(scope === 'today' ? 100 : 50)
  const [sort, onSort, setSort] = useSort<VisitSortKey>(null, () => setPage(1))
  useEffect(() => {
    setPage(1); setSort(null); setPageSize(scope === 'today' ? 100 : 50)
  }, [scope, setSort])
  const filters = scope === 'today'
    ? { date_from: today, date_to: today }
    : {}

  const { data, isLoading, error } = useVisits({ ...filters, page, page_size: pageSize, ...sortParams(sort) })
  const items = data?.items ?? []
  const total = data?.total ?? 0

  const title = scope === 'today' ? 'Today’s Visits' : 'All Visits'
  const subtitle = scope === 'today'
    ? `${new Date().toLocaleDateString('en-CA', { weekday: 'long', timeZone: displayTimeZone() })}, ${formatDate(new Date())}`
    : `${data?.total ?? 0} total`

  return (
    <div>
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-fg">{title}</h1>
          <p className="mt-1 text-sm text-fg-muted">{subtitle}</p>
        </div>
        <Link
          to="/new"
          className="inline-flex items-center gap-1.5 rounded-md bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700"
        >
          <Plus className="h-4 w-4" />
          New Visit
        </Link>
      </div>

      {error && (
        <div className="mt-4 flex items-center gap-2 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          <AlertCircle className="h-4 w-4" />
          {error.message}
        </div>
      )}

      <div className="mt-6 overflow-hidden rounded-lg border border-line bg-surface">
        <Table density="compact" className="min-w-full divide-y divide-line">
          <THead>
            <Tr>
              <SortTh label="Visitor" sortKey="visitor" sort={sort} onSort={onSort} className="px-4 py-2.5" />
              <SortTh label="Company" sortKey="company" sort={sort} onSort={onSort} className="px-4 py-2.5 hidden md:table-cell" />
              <SortTh label="Planned Arrival" sortKey="planned_arrival" sort={sort} onSort={onSort} className="px-4 py-2.5 hidden md:table-cell" />
              <SortTh label="Area" sortKey="access_area" sort={sort} onSort={onSort} className="px-4 py-2.5" />
              <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-4 py-2.5" />
            </Tr>
          </THead>
          <TBody className="text-sm">
            {isLoading && (
              <Tr><Td align="center" colSpan={5} className="text-fg-muted">Loading…</Td></Tr>
            )}
            {!isLoading && items.length === 0 && (
              <Tr>
                <Td align="center" colSpan={5} className="text-fg-muted">
                  No visits {scope === 'today' ? 'today' : 'on record'} yet.
                </Td>
              </Tr>
            )}
            {items.map((v: Visit) => (
              <Tr key={v.id} className="hover:bg-primary-50/30">
                <Td>
                  <Link to={`/${v.id}`} className="text-primary-700 hover:underline">
                    {v.visitor
                      ? `${v.visitor.first_name} ${v.visitor.last_name}`
                      : `Visit #${v.id.slice(0, 8)}`}
                  </Link>
                </Td>
                <Td className="hidden md:table-cell text-fg-secondary">
                  {v.visitor?.company_name ?? '—'}
                </Td>
                <Td className="hidden md:table-cell text-fg-secondary">
                  {formatDateTime(v.planned_arrival)}
                </Td>
                <Td>
                  <AccessAreaBadge area={v.access_area} />
                </Td>
                <Td>
                  <span className="inline-flex items-center gap-1.5">
                    <StatusBadge status={v.status} />
                    {isVisitOverdue(v) && <OverdueBadge />}
                  </span>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
        {total > 0 && (
          <Pagination
            page={page} pageSize={pageSize} total={total}
            onPageChange={setPage}
            onPageSizeChange={(s) => { setPageSize(s); setPage(1) }}
          />
        )}
      </div>
    </div>
  )
}
