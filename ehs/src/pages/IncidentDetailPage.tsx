/**
 * One incident: what is being done about it, and what the record shows.
 *
 * Two columns on a desk — the cause tree with its corrective actions on the
 * left, the history and the statutory clocks on the right. They stack on a
 * phone. A root cause with nothing under it is flagged by the API, and the
 * screen shows that rather than leaving a reader to notice the absence.
 */
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, FileText, Plus } from 'lucide-react'
import { api, apiBlob, ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { Countdown } from '@/components/Countdown'
import { InjuryBadge, MolBadge, StatusBadge } from '@/components/StatusBadge'
import { usePermissions } from '@/hooks/usePermissions'
import { NewActionDialog } from '@/components/NewActionDialog'
import { RiskAssessmentDialog } from '@/components/RiskAssessmentDialog'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import type { Deadline } from '@/lib/types'

interface Person {
  id: string; role: string; person_name: string
  external_company: string | null; body_part_label: string | null
  nature_of_injury_label: string | null; treatment: string | null
}
interface Incident {
  id: string; incident_no: string; form_kind: string; title: string; status: string
  occurred_at: string | null; employer_aware_at: string | null; location_path: string | null
  injury_class: string | null; mol_reportable: boolean; mol_reportable_reason: string | null
  description: string | null; equipment_involved: string | null
  witnesses_note: string | null; immediate_action_taken: string | null
  reported_by_name: string | null; reported_to_name: string | null; is_anonymous: boolean
  signature: string | null
  signer_name: string | null; signer_role: string | null; signed_at: string | null
  hse_reviewed_at: string | null; hse_reviewed_by_name: string | null
  /** Answers belonging to one form kind only — the near-miss operator questions. */
  extra: Record<string, unknown>
  classified_at: string | null; classified_by_name: string | null
  submitted_at: string | null; closed_at: string | null; created_at: string
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
interface Attachment {
  id: string; filename: string; content_type: string
  file_size: number; uploaded_by_name: string | null; created_at: string
}
interface Form7 {
  is_complete: boolean
  missing: { field: string; why_it_matters: string }[]
  due_at: string | null
  internal_target: string | null
  filed_at: string | null
  confirmation_number: string | null
}

const KIND_LABELS: Record<string, string> = {
  medical: 'Injury or illness',
  equipment: 'Equipment',
  near_miss: 'Near miss or good catch',
}

// Near-miss forms store their two answers in `extra` rather than in columns of
// their own, since no other form kind asks them.
const NEAR_MISS_ANSWERS: [string, string][] = [
  ['talked_to_operator', 'Talked to the operator at the time'],
  ['why_not', 'Why not'],
]

function asText(v: unknown): string | null {
  if (v === null || v === undefined || v === '') return null
  return String(v)
}

/** Absent values are dropped rather than rendered as an em dash: a detail page
 *  padded with empty rows reads as though the report itself is empty. */
function fmt(iso: string | null | undefined): string | null {
  return iso ? formatDateTime(iso) : null
}

function Row({ label, value, block = false }: {
  label: string
  value: React.ReactNode
  block?: boolean
}) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className={block ? 'border-b border-neutral-100 py-2 last:border-0'
                          : 'flex gap-3 border-b border-neutral-100 py-2 last:border-0'}>
      <dt className={block ? 'text-xs font-medium uppercase tracking-wide text-neutral-400'
                           : 'w-40 shrink-0 text-xs text-neutral-500'}>
        {label}
      </dt>
      <dd className={block ? 'mt-1 whitespace-pre-wrap text-sm text-neutral-800'
                           : 'min-w-0 flex-1 text-sm text-neutral-800'}>
        {value}
      </dd>
    </div>
  )
}

/**
 * One attachment. The bytes come back through this service (so the request
 * carries a token and a deleted incident's files stop being reachable), which
 * means the URL cannot go straight into `src` — it is fetched and turned into
 * an object URL, then revoked when the tile goes away.
 */
function Photo({ incidentId, att }: { incidentId: string; att: Attachment }) {
  const [url, setUrl] = useState<string | null>(null)
  const isImage = att.content_type.startsWith('image/')

  useEffect(() => {
    if (!isImage) return
    let revoked = false
    let objectUrl: string | null = null
    apiBlob(`/api/v1/incidents/${incidentId}/attachments/${att.id}/download`)
      .then((blob) => {
        if (revoked) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      })
      .catch(() => setUrl(null))
    return () => {
      revoked = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [incidentId, att.id, isImage])

  return (
    <figure className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
      {isImage && url ? (
        <a href={url} target="_blank" rel="noreferrer">
          <img src={url} alt={att.filename}
               className="h-32 w-full object-cover transition-opacity hover:opacity-90" />
        </a>
      ) : (
        <div className="flex h-32 items-center justify-center bg-neutral-50 text-neutral-400">
          <FileText className="h-7 w-7" aria-hidden />
        </div>
      )}
      <figcaption className="truncate px-2 py-1.5 text-[11px] text-neutral-500">
        {att.filename}
        {att.uploaded_by_name ? ` · ${att.uploaded_by_name}` : ''}
      </figcaption>
    </figure>
  )
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

  // The route table names this tab before the fetch resolves, so it can only
  // use the id from the URL. Rename it to the incident number once we have one.
  useTabTitle(ehsRoutes, incident.data?.incident_no)

  const canInvestigate = can('ehs.incident.investigate')
  const canRaiseAction = can('ehs.action.write')
  const [assessing, setAssessing] = useState(false)
  const hasInvestigation = Boolean(
    tree.data && (tree.data.immediate.length > 0 || tree.data.root.length > 0))

  const photos = useQuery({
    queryKey: ['incident', id, 'attachments'],
    queryFn: () => api.get<Attachment[]>(`/api/v1/incidents/${id}/attachments`),
    enabled: Boolean(id),
  })

  const canClose = can('ehs.incident.close')

  const act = (verb: 'hse-review' | 'close') => ({
    mutationFn: () => api.post(`/api/v1/incidents/${id}/${verb}`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['incident', id] }),
  })
  const review = useMutation(act('hse-review'))
  const closeIncident = useMutation(act('close'))

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
        <div className="min-w-0">
        {/* ── What was reported ─────────────────────────────────────────── */}
        <section className="p-4">
          <h2 className="mb-2.5 text-xs font-bold uppercase tracking-wider text-neutral-400">
            What was reported
          </h2>
          <dl className="rounded-lg border border-neutral-200 bg-white p-3.5">
            <Row label="Type" value={KIND_LABELS[inc.form_kind] ?? inc.form_kind} />
            <Row label="Description" value={inc.description} block />
            <Row label="Equipment" value={inc.equipment_involved} />
            <Row label="Witnesses" value={inc.witnesses_note} block />
            <Row label="Action taken at the time" value={inc.immediate_action_taken} block />
            <Row label="Reported to" value={inc.reported_to_name} />
            {NEAR_MISS_ANSWERS.map(([key, label]) => (
              <Row key={key} label={label} value={asText(inc.extra?.[key])} block />
            ))}
            {inc.mol_reportable_reason && (
              <Row label="Why it is MOL-reportable" value={inc.mol_reportable_reason} block />
            )}
            <Row
              label="Signature"
              value={inc.signature ? (
                <div>
                  <img src={inc.signature} alt="Reporter's signature"
                       className="max-h-16 rounded border border-neutral-200 bg-white p-1" />
                  {/* Attribution under the mark, the way it reads on paper. Role
                      is omitted rather than guessed when nobody has set one. */}
                  <p className="mt-1 text-xs text-neutral-500">
                    {[inc.signer_name, inc.signer_role].filter(Boolean).join(', ')}
                    {inc.signed_at ? ` · ${formatDateTime(inc.signed_at)}` : ''}
                  </p>
                </div>
              ) : null}
            />
          </dl>
        </section>

        {/* ── Photographs ───────────────────────────────────────────────── */}
        {photos.data && photos.data.length > 0 && (
          <section className="px-4 pb-4">
            <h2 className="mb-2.5 text-xs font-bold uppercase tracking-wider text-neutral-400">
              Photographs
            </h2>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {photos.data.map((a) => <Photo key={a.id} incidentId={id} att={a} />)}
            </div>
          </section>
        )}

        {/* ── Causes and what is being done ─────────────────────────────── */}
        <section className="p-4">
          {canInvestigate && (
            <Link
              to={`/incidents/${id}/investigate`}
              className="mb-3 flex min-h-[40px] w-full items-center justify-center rounded-lg border border-primary-200 bg-primary-50 px-4 text-sm font-medium text-primary-700 hover:bg-primary-100"
            >
              {hasInvestigation ? 'Edit the investigation' : 'Start the investigation'}
            </Link>
          )}
          {canInvestigate && (
            <button
              type="button"
              onClick={() => setAssessing(true)}
              className="mb-3 flex min-h-[40px] w-full items-center justify-center rounded-lg border border-neutral-300 px-4 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
            >
              Assess the risk
            </button>
          )}
          {assessing && (
            <RiskAssessmentDialog sourceType="incident" sourceId={id}
                                  onClose={() => setAssessing(false)} />
          )}
          <h2 className="mb-2.5 text-xs font-bold uppercase tracking-wider text-neutral-400">
            Root causes and what is being done
          </h2>
          {tree.isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
          {tree.data && tree.data.immediate.length === 0 && tree.data.root.length === 0 && (
            <p className="rounded-lg border border-dashed border-neutral-300 p-4 text-sm text-neutral-500">
              No causes recorded yet. They are added during the investigation.
            </p>
          )}
          {tree.data?.immediate.map((c) => (
            <CauseCard key={c.id} cause={c} incidentId={id} canRaise={canRaiseAction} />
          ))}
          {tree.data && tree.data.root.length > 0 && (
            <div className="ml-4 border-l-2 border-neutral-200 pl-3">
              {tree.data.root.map((c) => (
                <CauseCard key={c.id} cause={c} incidentId={id} canRaise={canRaiseAction} />
              ))}
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

        </div>

        {/* ── The record ────────────────────────────────────────────────── */}
        <section className="border-t border-neutral-200 bg-neutral-50 p-4 lg:border-l lg:border-t-0">
          {canClose && inc.status !== 'closed' && (
            <div className="mb-4 rounded-lg border border-neutral-200 bg-white p-3">
              <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-neutral-400">
                Closing this incident
              </h2>
              {inc.hse_reviewed_at ? (
                <p className="mb-2 text-xs text-neutral-600">
                  Reviewed by {inc.hse_reviewed_by_name ?? 'HSE'} on{' '}
                  {formatDateTime(inc.hse_reviewed_at)}.
                </p>
              ) : (
                <p className="mb-2 text-xs text-neutral-600">
                  An injury or equipment-damage incident cannot be closed until HSE
                  has reviewed it.
                </p>
              )}
              <div className="flex gap-2">
                {!inc.hse_reviewed_at && (
                  <button
                    type="button"
                    disabled={review.isPending}
                    onClick={() => { if (!review.isPending) review.mutate() }}
                    className="min-h-[38px] flex-1 rounded-lg border border-neutral-300 px-3 text-xs font-medium text-neutral-700 disabled:opacity-50"
                  >
                    {review.isPending ? 'Recording…' : 'Record HSE review'}
                  </button>
                )}
                <button
                  type="button"
                  disabled={closeIncident.isPending}
                  onClick={() => { if (!closeIncident.isPending) closeIncident.mutate() }}
                  className="min-h-[38px] flex-1 rounded-lg bg-primary-600 px-3 text-xs font-semibold text-white disabled:opacity-50"
                >
                  {closeIncident.isPending ? 'Closing…' : 'Close incident'}
                </button>
              </div>
              {closeIncident.isError && (
                <p className="mt-2 text-xs text-danger-700">
                  {(closeIncident.error as ApiError).message}
                </p>
              )}
            </div>
          )}

          <h2 className="mb-2.5 text-xs font-bold uppercase tracking-wider text-neutral-400">
            Timeline
          </h2>
          <dl className="mb-4 rounded-lg border border-neutral-200 bg-white p-3.5">
            <Row label="Occurred" value={fmt(inc.occurred_at)} />
            {/* Kept distinct from `occurred` on purpose: the WSIB clock runs
                from awareness, the MOL clock from the occurrence itself. */}
            <Row label="Employer aware" value={fmt(inc.employer_aware_at)} />
            <Row label="Reported" value={fmt(inc.submitted_at ?? inc.created_at)} />
            <Row
              label="Classified"
              value={inc.classified_at
                ? `${fmt(inc.classified_at)}${inc.classified_by_name ? ` · ${inc.classified_by_name}` : ''}`
                : null}
            />
            <Row
              label="HSE reviewed"
              value={inc.hse_reviewed_at
                ? `${fmt(inc.hse_reviewed_at)}${inc.hse_reviewed_by_name ? ` · ${inc.hse_reviewed_by_name}` : ''}`
                : null}
            />
            <Row label="Closed" value={fmt(inc.closed_at)} />
          </dl>

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
                  {p.external_company && (
                    <span className="ml-1.5 text-xs text-neutral-500">({p.external_company})</span>
                  )}
                  <span className="ml-1.5 text-xs text-neutral-500">
                    {[p.role, p.body_part_label, p.nature_of_injury_label]
                      .filter(Boolean).join(' · ')}
                  </span>
                  {p.treatment && (
                    <p className="mt-0.5 text-xs text-neutral-600">{p.treatment}</p>
                  )}
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

function CauseCard({ cause, incidentId, canRaise }: {
  cause: Cause; incidentId: string; canRaise: boolean
}) {
  const [raising, setRaising] = useState(false)

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

      {/* Raised from the cause, so the action carries `cause_id` and the flag
          above clears. Doing it from the actions list means re-keying which
          cause it belongs to, which is how causes stay flagged after the work
          has actually been done. */}
      {canRaise && (
        <button
          type="button"
          onClick={() => setRaising(true)}
          className="mt-2 flex min-h-[36px] items-center gap-1.5 text-xs font-medium text-primary-700"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          Raise a corrective action
        </button>
      )}

      {raising && (
        <NewActionDialog
          incidentId={incidentId}
          causeId={cause.id}
          causeLabel={cause.label}
          onClose={() => setRaising(false)}
        />
      )}
    </article>
  )
}
