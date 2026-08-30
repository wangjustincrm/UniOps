/**
 * One incident: what is being done about it, and what the record shows.
 *
 * Two columns on a desk — the cause tree with its corrective actions on the
 * left, the history and the statutory clocks on the right. They stack on a
 * phone. A root cause with nothing under it is flagged by the API, and the
 * screen shows that rather than leaving a reader to notice the absence.
 */
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, FileText } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { Countdown } from '@/components/Countdown'
import { InjuryBadge, MolBadge, StatusBadge } from '@/components/StatusBadge'
import { usePermissions } from '@/hooks/usePermissions'
import type { Deadline } from '@/lib/types'

interface Person { id: string; role: string; person_name: string; body_part_label: string | null }
interface Incident {
  id: string; incident_no: string; form_kind: string; title: string; status: string
  occurred_at: string | null; employer_aware_at: string | null; location_path: string | null
  injury_class: string | null; mol_reportable: boolean; description: string | null
  reported_by_name: string | null; is_anonymous: boolean
  persons: Person[]; deadlines: Deadline[]
}
interface CauseAction {
  id: string; action_no: string; title: string; status: string
  owner_name: string | null; due_date: string; escalation_level: number
}
interface Cause {
  id: string; cause_type: string; label: string; note: string | null
  actions: CauseAction[]; needs_action: boolean
}
interface CauseTree {
  incident_no: string; immediate: Cause[]; root: Cause[]; unaddressed_root_causes: number
}
interface Form7 {
  is_complete: boolean
  missing: { field: string; why_it_matters: string }[]
  due_at: string | null
  internal_target: string | null
  filed_at: string | null
  confirmation_number: string | null
}

export default function IncidentDetailPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [confirmation, setConfirmation] = useState('')

  const incident = useQuery({
    queryKey: ['incident', id],
    queryFn: () => api.get<Incident>(`/api/v1/incidents/${id}`),
  })
  const tree = useQuery({
    queryKey: ['incident', id, 'causes'],
    queryFn: () => api.get<CauseTree>(`/api/v1/incidents/${id}/cause-tree`),
    enabled: Boolean(id),
  })
  const form7 = useQuery({
    queryKey: ['incident', id, 'wsib'],
    queryFn: () => api.get<Form7>(`/api/v1/incidents/${id}/wsib-form7`),
    enabled: Boolean(incident.data?.injury_class &&
      ['medical_aid', 'lost_time'].includes(incident.data.injury_class)),
  })

  const recordFiling = useMutation({
    mutationFn: () => api.post(`/api/v1/incidents/${id}/wsib-form7/filed`,
                               { confirmation_number: confirmation }),
    onSuccess: () => {
      setConfirmation('')
      queryClient.invalidateQueries({ queryKey: ['incident', id] })
    },
  })

  if (incident.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (incident.isError) {
    return <p className="p-4 text-sm text-danger-700">{(incident.error as ApiError).message}</p>
  }
  const inc = incident.data!

  return (
    <div className="min-w-0">
      <header className="border-b border-neutral-200 bg-white p-4">
        <p className="font-mono text-xs text-neutral-500">{inc.incident_no}</p>
        <h1 className="mt-0.5 text-lg font-bold tracking-tight text-neutral-900">{inc.title}</h1>
        <p className="mt-1 text-xs text-neutral-500">
          {inc.location_path ?? 'No area recorded'}
          {inc.occurred_at ? ` · ${formatDateTime(inc.occurred_at)}` : ''}
          {inc.is_anonymous ? ' · reported anonymously' : inc.reported_by_name ? ` · ${inc.reported_by_name}` : ''}
        </p>
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          <InjuryBadge injuryClass={inc.injury_class} />
          <MolBadge reportable={inc.mol_reportable} />
          <StatusBadge status={inc.status} />
          {inc.deadlines.map((d) => (
            <Countdown key={d.id} kind={d.kind} dueAt={d.due_at} satisfiedAt={d.satisfied_at} />
          ))}
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_0.8fr]">
        {/* ── Causes and what is being done ─────────────────────────────── */}
        <section className="p-4">
          <h2 className="mb-2.5 text-xs font-bold uppercase tracking-wider text-neutral-400">
            Root causes and what is being done
          </h2>
          {tree.isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
          {tree.data && tree.data.immediate.length === 0 && tree.data.root.length === 0 && (
            <p className="rounded-lg border border-dashed border-neutral-300 p-4 text-sm text-neutral-500">
              No causes recorded yet. They are added during the investigation.
            </p>
          )}
          {tree.data?.immediate.map((c) => <CauseCard key={c.id} cause={c} />)}
          {tree.data && tree.data.root.length > 0 && (
            <div className="ml-4 border-l-2 border-neutral-200 pl-3">
              {tree.data.root.map((c) => <CauseCard key={c.id} cause={c} />)}
            </div>
          )}
          {tree.data && tree.data.unaddressed_root_causes > 0 && (
            <p className="mt-2 flex items-start gap-2 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning-700">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {tree.data.unaddressed_root_causes} root{' '}
              {tree.data.unaddressed_root_causes === 1 ? 'cause has' : 'causes have'} no
              corrective action. An auditor reads that as an unfinished investigation.
            </p>
          )}
        </section>

        {/* ── The record ────────────────────────────────────────────────── */}
        <section className="border-t border-neutral-200 bg-neutral-50 p-4 lg:border-l lg:border-t-0">
          <h2 className="mb-2.5 text-xs font-bold uppercase tracking-wider text-neutral-400">
            People
          </h2>
          {inc.persons.length === 0 ? (
            <p className="text-sm text-neutral-500">Nobody recorded.</p>
          ) : (
            <ul className="mb-4 flex flex-col gap-1.5">
              {inc.persons.map((p) => (
                <li key={p.id} className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm">
                  <span className="font-medium text-neutral-900">{p.person_name}</span>
                  <span className="ml-1.5 text-xs text-neutral-500">
                    {p.role}{p.body_part_label ? ` · ${p.body_part_label}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <h2 className="mb-2.5 text-xs font-bold uppercase tracking-wider text-neutral-400">
            Statutory
          </h2>
          {inc.deadlines.length === 0 ? (
            <p className="text-sm text-neutral-500">
              Nothing is owed to a regulator — this has not been classified as a
              reportable injury.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {inc.deadlines.map((d) => (
                <li key={d.id} className="rounded-lg border border-neutral-200 bg-white p-3">
                  <Countdown kind={d.kind} dueAt={d.due_at} satisfiedAt={d.satisfied_at} />
                  <p className="mt-1.5 text-xs text-neutral-500">
                    {d.regulation_ref ?? 'Statutory obligation'} · due {formatDateTime(d.due_at)}
                  </p>
                </li>
              ))}
            </ul>
          )}

          {form7.data && (
            <div className="mt-4 rounded-lg border border-neutral-200 bg-white p-3">
              <h3 className="flex items-center gap-1.5 text-sm font-semibold text-neutral-900">
                <FileText className="h-4 w-4 text-neutral-400" aria-hidden />
                WSIB Form 7
              </h3>
              {form7.data.filed_at ? (
                <p className="mt-1.5 text-xs text-success-700">
                  Filed {formatDateTime(form7.data.filed_at)} · reference{' '}
                  {form7.data.confirmation_number}
                </p>
              ) : (
                <>
                  <p className="mt-1.5 text-xs text-neutral-500">
                    UniOps does not submit Form 7 — file it in WSIB's own service,
                    then record the reference here to stop the clock.
                  </p>
                  {!form7.data.is_complete && (
                    <ul className="mt-2 flex flex-col gap-1">
                      {form7.data.missing.map((m) => (
                        <li key={m.field} className="text-xs text-warning-700">
                          <span className="font-medium">{m.field.replace(/_/g, ' ')}</span> —{' '}
                          {m.why_it_matters}
                        </li>
                      ))}
                    </ul>
                  )}
                  {can('ehs.statutory.manage') && (
                    <div className="mt-2.5 flex gap-2">
                      <input
                        className="min-h-[40px] flex-1 rounded-lg border border-neutral-300 px-2.5 text-sm"
                        placeholder="WSIB reference"
                        value={confirmation}
                        onChange={(e) => setConfirmation(e.target.value)}
                      />
                      <button
                        type="button"
                        disabled={!confirmation.trim() || recordFiling.isPending}
                        onClick={() => { if (!recordFiling.isPending) recordFiling.mutate() }}
                        className="min-h-[40px] rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white disabled:opacity-50"
                      >
                        Record
                      </button>
                    </div>
                  )}
                  {recordFiling.isError && (
                    <p className="mt-1.5 text-xs text-danger-700">
                      {(recordFiling.error as ApiError).message}
                    </p>
                  )}
                </>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function CauseCard({ cause }: { cause: Cause }) {
  return (
    <article className="mb-2 rounded-lg border border-neutral-200 bg-white p-3">
      <p className="font-mono text-[10px] uppercase tracking-wider text-neutral-400">
        {cause.cause_type === 'root' ? 'Root cause' : 'Immediate cause'}
      </p>
      <p className="mt-0.5 text-sm text-neutral-900">{cause.label}</p>
      {cause.note && <p className="mt-1 text-xs text-neutral-500">{cause.note}</p>}

      {cause.actions.map((a) => (
        <div key={a.id}
             className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-dashed border-neutral-200 bg-neutral-50 px-2.5 py-2 text-xs">
          <span className="font-mono text-neutral-500">{a.action_no}</span>
          <span className="min-w-0 flex-1 text-neutral-800">{a.title}</span>
          <StatusBadge status={a.status} />
          {a.owner_name && <span className="text-neutral-500">{a.owner_name}</span>}
        </div>
      ))}

      {cause.needs_action && (
        <p className="mt-2 rounded-md bg-warning-50 px-2.5 py-1.5 text-xs font-medium text-warning-700">
          No corrective action attached
        </p>
      )}
    </article>
  )
}
