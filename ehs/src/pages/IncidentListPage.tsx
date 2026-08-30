/**
 * The incident register, ordered by what is running out rather than by date.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import type { IncidentListItem } from '@/lib/types'
import { InjuryBadge, MolBadge, StatusBadge } from '@/components/StatusBadge'

const KIND_LABELS: Record<string, string> = {
  medical: 'Injury',
  equipment: 'Equipment',
  near_miss: 'Near miss',
}

export default function IncidentListPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['incidents'],
    queryFn: () => api.get<IncidentListItem[]>('/api/v1/incidents'),
  })

  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading incidents…</p>
  if (isError) return <p className="p-4 text-sm text-danger-700">{(error as Error).message}</p>

  const incidents = data ?? []
  if (incidents.length === 0) {
    return (
      <p className="p-10 text-center text-sm text-neutral-500">
        No incidents recorded yet.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-2 p-4">
      {incidents.map((i) => (
        <article key={i.id} className="rounded-xl border border-neutral-200 bg-white p-3.5">
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
        </article>
      ))}
    </div>
  )
}
