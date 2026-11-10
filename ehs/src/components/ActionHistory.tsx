/**
 * What happened to each corrective action raised from a record, on the record.
 *
 * ★ Round 12, JHSC-01: "when a capa is created from any form, the history that
 * the capa has gone through needs to be recorded on the history of the record
 * (form) that created it. JHSC 2026-0013 has a recommendation created, and capa
 * 2026-0096 was created to manage the work. the JHSC capa had 2 progress
 * updates and a final verified effective completion status update. this
 * detailed history should be recorded on the original JHSC recommendation."
 * Round 4 had kept that history on the action alone.
 *
 * By source, or by action id for a JHSC recommendation (its actions' source is
 * the meeting, which holds several recommendations).
 */
import { useQuery } from '@tanstack/react-query'
import TabLink from '@/components/TabLink'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { LoadFailedNote, SectionTitle } from '@/components/ui'
import { StatusBadge } from '@/components/StatusBadge'

interface Entry {
  at: string; what: string; by: string | null; text: string | null; new_status: string | null
}
interface History {
  id: string; action_no: string; title: string; status: string
  owner_name: string | null; entries: Entry[]
}

const WHAT: Record<string, string> = {
  raised: 'Raised',
  update: 'Progress',
  reassigned: 'Reassigned',
  verified: 'Verified effective',
  not_effective: 'Not effective — reopened',
}

function label(e: Entry): string {
  if (e.what === 'update' && e.new_status === 'pending_verification') return 'Work done'
  return WHAT[e.what] ?? e.what
}

export function ActionHistory({ sourceType, sourceId, actionIds, title = 'Corrective action history' }: {
  sourceType?: string
  sourceId?: string | null
  actionIds?: (string | null | undefined)[]
  title?: string
}) {
  const ids = (actionIds ?? []).filter((x): x is string => !!x)
  const qs = new URLSearchParams()
  if (ids.length) ids.forEach((i) => qs.append('action_id', i))
  else if (sourceType && sourceId) { qs.set('source_type', sourceType); qs.set('source_id', sourceId) }
  const enabled = ids.length > 0 || Boolean(sourceType && sourceId)
  const history = useQuery({
    queryKey: ['action-history', sourceType, sourceId, ids.join(',')],
    queryFn: () => api.get<History[]>(`/api/v1/actions/history?${qs}`),
    enabled,
  })
  if (!enabled) return null
  if (history.isError) return <LoadFailedNote what="the corrective action history" />
  if (!history.data?.length) return null
  return (
    <section className="mt-5">
      <SectionTitle className="mb-2">{title}</SectionTitle>
      <div className="flex flex-col gap-3">
        {history.data.map((a) => (
          <div key={a.id} className="rounded-lg border border-neutral-200 bg-white p-3">
            <div className="flex flex-wrap items-center gap-2">
              <TabLink to={`/actions/${a.id}`}
                       className="font-mono text-xs font-semibold text-primary-700 hover:underline">
                {a.action_no}
              </TabLink>
              <span className="min-w-0 flex-1 text-sm font-medium text-neutral-900">{a.title}</span>
              <StatusBadge status={a.status} />
            </div>
            <ol className="mt-2 flex flex-col gap-1.5 border-l-2 border-neutral-100 pl-3">
              {a.entries.map((e, i) => (
                <li key={i} className="text-xs">
                  <span className="font-medium text-neutral-800">{label(e)}</span>
                  <span className="text-neutral-500">
                    {' · '}{formatDateTime(e.at)}{e.by ? ` · ${e.by}` : ''}
                  </span>
                  {e.text && <p className="mt-0.5 whitespace-pre-wrap text-neutral-700">{e.text}</p>}
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </section>
  )
}
