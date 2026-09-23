/**
 * The incident register, ordered by what is running out rather than by date.
 *
 * Serves two audiences from one page, because the endpoint does: with
 * `ehs.incident.read` it returns every incident, and without it, only the ones
 * you filed. The heading and the empty state say which list you are looking at
 * — "no incidents at the plant" and "you have not filed one" are very
 * different statements to put in front of a worker.
 */
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { usePermissions } from '@/hooks/usePermissions'
import { formatDateTime } from '@/lib/formatDate'
import type { IncidentListItem } from '@/lib/types'
import { InjuryBadge, MolBadge, StatusBadge } from '@/components/StatusBadge'
import { Pagination, SearchBox, useListView } from '@/components/ListView'

const KIND_LABELS: Record<string, string> = {
  medical: 'Injury',
  equipment: 'Equipment',
  near_miss: 'Near miss',
}

export default function IncidentListPage() {
  const { can } = usePermissions()
  const isRegister = can('ehs.incident.read')
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['incidents'],
    queryFn: () => api.get<IncidentListItem[]>('/api/v1/incidents'),
  })

  // ★ Above the early returns. A hook below one runs on some renders and not
  // others, which is what took the dashboard down.
  const list = useListView(data, (i) => [
    i.incident_no, i.title, i.location_path, i.status, KIND_LABELS[i.form_kind],
  ])

  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading incidents…</p>
  if (isError) return <p className="p-4 text-sm text-danger-700">{(error as Error).message}</p>

  const incidents = data ?? []
  const heading = isRegister ? 'Incidents' : 'My Reports'
  // The endpoint returns the fifty most recent by default. Say so rather than
  // letting the count read as the whole register.
  const capped = incidents.length >= 50

  if (incidents.length === 0) {
    return (
      <div className="p-4">
        <h1 className="text-base font-semibold text-neutral-900">{heading}</h1>
        <p className="p-10 text-center text-sm text-neutral-500">
          {isRegister
            ? 'No incidents recorded yet.'
            : 'You have not reported an incident yet.'}
        </p>
      </div>
    )
  }

  return (
    <div className="p-4">
      <h1 className="mb-3 text-base font-semibold text-neutral-900">{heading}</h1>

      <SearchBox value={list.query} onChange={list.setQuery}
                 placeholder="Search by number, title, area or status" />

      <div className="flex flex-col gap-2">
      {list.pageRows.map((i) => (
        <Link
          key={i.id}
          to={`/incidents/${i.id}`}
          className="block rounded-xl border border-neutral-200 bg-white p-3.5 transition-colors hover:border-primary-300 hover:bg-primary-50/40"
        >
          <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-neutral-900">{i.title}</h2>
              <p className="mt-0.5 text-xs text-neutral-500">
                {i.incident_no} · {KIND_LABELS[i.form_kind] ?? i.form_kind}
                {i.location_path ? ` · ${i.location_path}` : ''}
                {i.occurred_at ? ` · ${formatDateTime(i.occurred_at)}` : ''}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <InjuryBadge injuryClass={i.injury_class} />
              <MolBadge reportable={i.mol_reportable} />
              <StatusBadge status={i.status} />
            </div>
          </div>
        </Link>
      ))}
      </div>

      <Pagination page={list.page} pageCount={list.pageCount} total={list.total}
                  onPage={list.setPage} capped={capped} noun="incidents" />
    </div>
  )
}
