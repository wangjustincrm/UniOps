import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Loader2, UserCheck, Users } from 'lucide-react'
import { SortTh, useSortableRows, TBody, THead, Table, Td, Tr, Button } from '@uniops/shell'
import { useActiveVisits, useAreaRules, useBatchCheckOut } from '@/services/api'
import { AccessAreaBadge, OverdueBadge, accessAreaLabel, isVisitOverdue } from '@/components/StatusBadge'
import { formatDateTime, timeAgo } from '@/lib/utils'

function getRole(): string | null {
  try {
    for (const key of ['vms-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const state = raw ? JSON.parse(raw)?.state : null
      if (state?.user?.role) return state.user.role
    }
    return null
  } catch { return null }
}

export default function ActiveVisitsPage() {
  const { data, isLoading } = useActiveVisits()
  const { data: areaRules } = useAreaRules()
  const items = data ?? []
  // Whole list (/visits/active is not paged) → sorted here.
  const sorted = useSortableRows(data, {
    visit: (v) => (v.visitor ? `${v.visitor.first_name} ${v.visitor.last_name}` : `Visit #${v.id.slice(0, 8)}`),
    area: (v) => accessAreaLabel(v.access_area, areaRules),
    checked_in: (v) => v.actual_arrival,
    duration: (v) => (v.actual_arrival ? Date.now() - Date.parse(v.actual_arrival) : null),
  })
  const isAdmin = getRole() === 'system_admin'

  const batch = useBatchCheckOut()
  const [confirming, setConfirming] = useState(false)

  const onBatchClose = () => {
    if (!confirm(`Close all ${items.length} on-site visitors now? They will all be marked checked out.`)) {
      return
    }
    setConfirming(true)
    batch.mutate(undefined, {
      onSettled: () => setConfirming(false),
    })
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <UserCheck className="h-6 w-6 text-primary-600" />
          <h1 className="text-2xl font-semibold text-fg">On-Site Now</h1>
          {!isLoading && (
            <span className="rounded-full bg-success-soft px-2.5 py-0.5 text-xs font-semibold text-success-fg ring-1 ring-inset ring-success-line">
              {items.length}
            </span>
          )}
        </div>

        {isAdmin && items.length > 0 && (
          <Button variant="secondary" size="sm"
            onClick={onBatchClose}
            disabled={confirming || batch.isPending}
           
          >
            {(confirming || batch.isPending)
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Users className="h-4 w-4" />}
            Batch check-out
          </Button>
        )}
      </div>

      {batch.error && (
        <p className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          {batch.error.message}
        </p>
      )}
      {batch.data && (
        <p className="mt-3 rounded-md bg-success-soft px-3 py-2 text-sm text-success-fg">
          Closed {batch.data.closed} visit{batch.data.closed === 1 ? '' : 's'}.
        </p>
      )}

      <div className="mt-6 overflow-hidden rounded-lg border border-line bg-surface">
        <Table density="compact" className="min-w-full divide-y divide-line">
          <THead>
            <Tr>
              <SortTh label="Visit" sortKey="visit" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-2.5" />
              <SortTh label="Area" sortKey="area" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-2.5" />
              <SortTh label="Checked In" sortKey="checked_in" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-2.5 hidden md:table-cell" />
              <SortTh label="Duration" sortKey="duration" sort={sorted.sort} onSort={sorted.onSort} className="px-4 py-2.5" />
            </Tr>
          </THead>
          <TBody className="text-sm">
            {isLoading && (
              <Tr><Td align="center" colSpan={4} className="text-fg-muted">Loading…</Td></Tr>
            )}
            {!isLoading && items.length === 0 && (
              <Tr>
                <Td align="center" colSpan={4} className="text-fg-muted">
                  No visitors are currently on-site.
                </Td>
              </Tr>
            )}
            {sorted.rows.map(v => (
              <Tr key={v.id} className="hover:bg-primary-50/30">
                <Td>
                  <span className="inline-flex items-center gap-1.5">
                    <Link to={`/${v.id}`} className="text-primary-700 hover:underline">
                      {v.visitor
                        ? `${v.visitor.first_name} ${v.visitor.last_name}`
                        : `Visit #${v.id.slice(0, 8)}`}
                    </Link>
                    {isVisitOverdue(v) && <OverdueBadge />}
                  </span>
                  {v.visitor?.company_name && (
                    <p className="text-xs text-fg-muted">{v.visitor.company_name}</p>
                  )}
                </Td>
                <Td>
                  <AccessAreaBadge area={v.access_area} />
                </Td>
                <Td className="hidden md:table-cell text-fg-secondary">
                  {formatDateTime(v.actual_arrival)}
                </Td>
                <Td className="text-fg-secondary">
                  {timeAgo(v.actual_arrival)}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </div>
    </div>
  )
}
