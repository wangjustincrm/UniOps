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
import {
  ErrorNote, LoadFailedNote, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'
import { formatDateTime } from '@/lib/formatDate'
import { Countdown } from '@/components/Countdown'
import { ChangeHistory } from '@/components/ChangeHistory'
import { InjuryBadge, MolBadge, StatusBadge } from '@/components/StatusBadge'
import { usePermissions } from '@/hooks/usePermissions'
import { InjurySummary } from '@/components/InjurySummary'
import { CommunicationLog } from '@/components/CommunicationLog'
import { ReturnToWorkPanel } from '@/components/ReturnToWorkPanel'
import { NewActionDialog } from '@/components/NewActionDialog'
import { RiskAssessmentDialog } from '@/components/RiskAssessmentDialog'
import { RatingBadge, RiskScoringHistory, useScorings } from '@/components/RiskScoring'
import { LocationSelect } from '@/components/LocationSelect'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import type { Deadline } from '@/lib/types'

interface Person {
  id: string; role: string; person_name: string
  external_company: string | null; body_part_label: string | null
  nature_of_injury_label: string | null; treatment: string | null
  sent_offsite?: boolean | null; follow_up?: string | null
}
interface Incident {
  id: string; incident_no: string; form_kind: string; title: string; status: string
  occurred_at: string | null; employer_aware_at: string | null; location_path: string | null
  injury_class: string | null; mol_reportable: boolean; mol_reportable_reason: string | null
  description: string | null; equipment_involved: string | null
  witnesses_note: string | null; immediate_action_taken: string | null
  reported_by_name: string | null; reported_to_name: string | null; is_anonymous: boolean
  /** HSE only, on an anonymous report: who filed it. Round 6, box 4. */
  anonymous_reporter_name: string | null
  location_id: string | null
  signature: string | null
  signer_name: string | null; signer_role: string | null; signed_at: string | null
  hse_reviewed_at: string | null; hse_reviewed_by_name: string | null
  reopened_at: string | null; reopened_by_name: string | null
  /** Answers belonging to one form kind only — the near-miss operator questions. */
  extra: Record<string, unknown>
  /** Which of HSE's twelve options the reporter picked, in words. The server
   *  derives it from the stored code so the list lives in one place. */
  incident_type_label: string | null
  incident_type_code?: string | null
  classified_at: string | null; classified_by_name: string | null
  submitted_at: string | null; closed_at: string | null; created_at: string
  persons: Person[]; deadlines: Deadline[]
  events?: { kind: string; at: string; by_name: string | null; detail: string | null }[]
}
interface CauseAction {
  id: string; action_no: string; title: string; description: string | null; status: string
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
  by_anonymous_reporter?: boolean
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

const EVENT_LABELS: Record<string, string> = {
  kind_changed: 'Kind of incident changed',
  reopened: 'Reopened',
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
        {att.by_anonymous_reporter ? ' · the reporter (anonymous)'
          : att.uploaded_by_name ? ` · ${att.uploaded_by_name}` : ''}
      </figcaption>
    </figure>
  )
}

export default function IncidentDetailPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [confirmation, setConfirmation] = useState('')
  const [molReference, setMolReference] = useState('')
  // Round 9: reopening needs a reason, kept on the timeline.
  const [reopenReason, setReopenReason] = useState('')


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
  // The current rating, for the heading. The list below is the history; this
  // is the answer to "how bad is this one" without reading it.
  const scorings = useScorings('incident', id ?? '')
  const latestRating = scorings.data?.[0]?.rating ?? null
  const hasInvestigation = Boolean(
    tree.data && (tree.data.immediate.length > 0 || tree.data.root.length > 0))

  const photos = useQuery({
    queryKey: ['incident', id, 'attachments'],
    queryFn: () => api.get<Attachment[]>(`/api/v1/incidents/${id}/attachments`),
    enabled: Boolean(id),
  })

  const showStatutory = Boolean(incident.data) &&
    (incident.data!.form_kind === 'medical' || incident.data!.deadlines.length > 0)
  const canClose = can('ehs.incident.close')
  const [editing, setEditing] = useState(false)

  // Everything that changes an incident refreshes the register too. It did not,
  // so closing one from here left the list showing the status it had before —
  // the second acceptance round read that as the close not having worked. With
  // `refetchOnWindowFocus` off and the tab shell keeping pages alive, the stale
  // row survives until a hard reload.
  const touched = () => {
    queryClient.invalidateQueries({ queryKey: ['incident', id] })
    queryClient.invalidateQueries({ queryKey: ['incidents'] })
    queryClient.invalidateQueries({ queryKey: ['deadlines'] })
  }

  const act = (verb: 'hse-review' | 'close') => ({
    mutationFn: () => api.post(`/api/v1/incidents/${id}/${verb}`, {}),
    onSuccess: touched,
  })
  const review = useMutation(act('hse-review'))
  const closeIncident = useMutation(act('close'))
  const reopen = useMutation({
    mutationFn: () => api.post(`/api/v1/incidents/${id}/reopen`, { reason: reopenReason.trim() }),
    onSuccess: () => { setReopenReason(''); touched() },
  })

  // Stopping the Ministry's clock. It is recorded, not performed: OHSA s.51(1)
  // is discharged by sending the report, and UniOps does not send it.
  const recordMolReport = useMutation({
    mutationFn: () => api.post(`/api/v1/incidents/${id}/mol-report/filed`,
                               { reference: molReference }),
    onSuccess: () => { setMolReference(''); touched() },
  })

  const recordFiling = useMutation({
    mutationFn: () => api.post(`/api/v1/incidents/${id}/wsib-form7/filed`,
                               { confirmation_number: confirmation }),
    onSuccess: () => { setConfirmation(''); touched() },
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
      <PageHeader title={inc.title} subtitle={<>{inc.location_path ?? 'No area recorded'} {inc.occurred_at ? ` · ${formatDateTime(inc.occurred_at)}` : ''} {inc.is_anonymous ? ' · reported anonymously' : inc.reported_by_name ? ` · ${inc.reported_by_name}` : ''}</>} />
        {/* ★ Round 6, box 4: "only the HSE role can see what user ID was used to
            submit the incident to allow for further private discussions." The
            server sends this to HSE and to nobody else. */}
        {inc.is_anonymous && inc.anonymous_reporter_name && (
          <p className="mt-1 text-xs text-neutral-600">
            Reported anonymously by <span className="font-medium">{inc.anonymous_reporter_name}</span>
            {' '}— shown to HSE only, for a private conversation.
          </p>
        )}
        {canInvestigate && inc.status !== 'draft' && inc.status !== 'closed' && !editing && (
          <button type="button" className="mt-2 text-xs font-semibold text-primary-700 hover:underline"
                  onClick={() => setEditing(true)}>
            Edit the report
          </button>
        )}
        {editing && <EditReport incident={inc} isHse={canClose} onDone={() => { setEditing(false); touched() }}
                                onCancel={() => setEditing(false)} />}
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
          <SectionTitle className="mb-2.5">What was reported</SectionTitle>
          <dl className="rounded-lg border border-neutral-200 bg-white p-3.5">
            <Row label="Form" value={KIND_LABELS[inc.form_kind] ?? inc.form_kind} />
            {/* ★ Round 4: "the kind of incident … not displayed on the
                submitted form". The row above is the form it arrived on; this
                is the choice the reporter made, and it was stored, read by the
                notifier and shown to nobody. */}
            <Row label="What kind of incident" value={inc.incident_type_label} />
            {canInvestigate && inc.status !== 'draft' && inc.status !== 'closed' && (
              <ChangeKind incident={inc} onDone={touched} />
            )}
            {/* ★ Named as the form names it. It was "Description", which is
                what the column is called — and the same round reported "what
                else should we know" as not displayed, when it was, under a
                heading nobody had typed anything under. */}
            <Row label="What else should we know" value={inc.description} block />
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
        {/* ★ Always a section, whatever the answer. It was drawn only when the
            list came back non-empty, so a report with no photographs and a
            request that failed looked the same as each other and the same as a
            screen that does not show photographs at all — which is how round 4
            read it. The same "loading or failed read as fact" shape this module
            has been through four times. */}
        <section className="px-4 pb-4">
          <SectionTitle className="mb-2.5">Photographs</SectionTitle>
          {photos.isError ? (
            <LoadFailedNote what="The photographs" />
          ) : photos.isLoading ? (
            <p className="text-sm text-neutral-500">Loading…</p>
          ) : (photos.data ?? []).length === 0 ? (
            <p className="text-sm text-neutral-500">None were attached to this report.</p>
          ) : (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {(photos.data ?? []).map((a) => <Photo key={a.id} incidentId={id} att={a} />)}
            </div>
          )}
        </section>

        {/* ── Risk assessment ───────────────────────────────────────────── */}
        {/* ★ Its own section, because it had none. The button was here and what
            it produced appeared nowhere: the assessment saved and the page was
            unchanged, which is indistinguishable from it having failed. The
            scorings are readable by anybody who can open the incident — they
            are part of the record — while making one still needs the
            investigator's permission. */}
        <section className="px-4 pb-4">
          <div className="mb-2.5 flex items-center justify-between gap-3">
            <SectionTitle className="mb-0">Risk assessment</SectionTitle>
            {latestRating && <RatingBadge rating={latestRating} />}
          </div>
          {canInvestigate && (
            <button
              type="button"
              onClick={() => setAssessing(true)}
              className="flex min-h-[40px] w-full items-center justify-center rounded-lg border border-neutral-300 px-4 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
            >
              {latestRating ? 'Assess it again' : 'Assess the risk'}
            </button>
          )}
          {assessing && (
            <RiskAssessmentDialog sourceType="incident" sourceId={id}
                                  onClose={() => setAssessing(false)} />
          )}
          <RiskScoringHistory sourceType="incident" sourceId={id} showHazard
            emptyNote={canInvestigate
              ? 'Not scored against the matrix yet. Scoring an incident is how a one-off becomes a ranked hazard.'
              : 'Not scored against the matrix yet.'} />
        </section>

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
          <SectionTitle className="mb-2.5">Root causes and what is being done</SectionTitle>
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
          {/* ★ Round-6 review: this read `canClose && status !== 'closed'`,
              which shut out its own closed branch — Reopen never rendered. */}
          {canClose && (
            <div className="mb-4 rounded-lg border border-neutral-200 bg-white p-3">
              <SectionTitle className="mb-2">
                {inc.status === 'closed' ? 'This incident is closed' : 'Closing this incident'}
              </SectionTitle>
              {inc.status === 'closed' ? (
                <>
                  <p className="text-xs text-neutral-500">
                    Closed records are read only. Reopening is recorded against
                    whoever does it, and costs the HSE review — it has to be
                    reviewed again before it closes.
                  </p>
                  {can('ehs.incident.close') && (
                    <label className="mt-2 block text-xs font-medium text-neutral-700">
                      Reason for reopening
                      <textarea
                        className="mt-1 block min-h-[60px] w-full rounded-lg border border-neutral-300 px-2.5 py-2 text-sm"
                        value={reopenReason}
                        onChange={(e) => setReopenReason(e.target.value)}
                        placeholder="What has to be corrected"
                      />
                    </label>
                  )}
                  {can('ehs.incident.close') && (
                    <button
                      type="button"
                      disabled={reopen.isPending || reopenReason.trim().length < 3}
                      onClick={() => { if (!reopen.isPending) reopen.mutate() }}
                      className="mt-2 min-h-[40px] rounded-lg border border-neutral-300 px-3 text-sm font-semibold text-neutral-700 disabled:opacity-50"
                    >
                      {reopen.isPending ? 'Reopening…' : 'Reopen'}
                    </button>
                  )}
                  {reopen.isError && (
                    <p className="mt-2 text-xs text-danger-700">
                      {(reopen.error as ApiError).message}
                    </p>
                  )}
                  {inc.reopened_at && (
                    <p className="mt-1.5 text-xs text-neutral-500">
                      Previously reopened {formatDateTime(inc.reopened_at)}
                      {inc.reopened_by_name ? ` by ${inc.reopened_by_name}` : ''}
                    </p>
                  )}
                </>
              ) : inc.hse_reviewed_at ? (
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
              {/* ★ Round 8, TC-11: "when trying to record a HSE review, place a
                  warning reminding the user why the incident can not be reviewed
                  just like trying to close." The review refused in silence —
                  its refusal was never shown. Both now say why, the same way. */}
              {review.isError && (
                <Refusal title="It cannot be reviewed yet" error={review.error} />
              )}
              {closeIncident.isError && (
                <Refusal title="It cannot be closed yet" error={closeIncident.error} />
              )}
            </div>
          )}

          <SectionTitle className="mb-2.5">Timeline</SectionTitle>
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
            {/* ★ Round 9: things that can happen more than once — the kind of
                incident changed, a reopening and why — who and when. */}
            {(inc.events ?? []).map((e, i) => (
              <Row key={`${e.kind}-${i}`}
                   label={EVENT_LABELS[e.kind] ?? e.kind}
                   value={<>
                     {fmt(e.at)}{e.by_name ? ` · ${e.by_name}` : ''}
                     {e.detail ? (
                       <span className="block text-xs text-neutral-600">{e.detail}</span>
                     ) : e.kind === 'reopened' ? (
                       <span className="block text-xs text-neutral-400">No reason recorded</span>
                     ) : null}
                   </>} />
            ))}
          </dl>

          <SectionTitle className="mb-2.5">People</SectionTitle>
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

          {/* Round 8: the injury and its treatment in one read-only place,
              under the person hurt — behind the medical permission. */}
          {can('ehs.incident.medical.read')
            && (inc.form_kind === 'medical' || inc.persons.some((p) => p.role === 'injured')) && (
            <InjurySummary incidentId={inc.id} title={inc.title} kind={inc.incident_type_label}
                           where={inc.location_path} description={inc.description}
                           persons={inc.persons} />
          )}

          {/* ★ Round 7: "only make the statutory, and return to work sections
              visible on the medical incident form." Kept on any incident that
              has a legal clock all the same — a running clock is never hidden. */}
          {showStatutory && (<>
          <SectionTitle className="mb-2.5">Statutory</SectionTitle>
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
                  {/* Every clock here needs a way to be stopped by whoever
                      discharged it. The Ministry's had none, so it ran on after
                      the report had gone — an overdue legal obligation on the
                      screen that had actually been met. */}
                  {d.kind === 'mol_48h' && !d.satisfied_at && can('ehs.statutory.manage') && (
                    <div className="mt-2.5">
                      <p className="text-xs text-neutral-500">
                        UniOps does not notify the Ministry — send the written report,
                        then record where it went to stop the clock.
                      </p>
                      <div className="mt-2 flex gap-2">
                        <input
                          className="min-h-[40px] flex-1 rounded-lg border border-neutral-300 px-2.5 text-sm"
                          placeholder="Where it was sent, and any acknowledgement"
                          value={molReference}
                          onChange={(e) => setMolReference(e.target.value)}
                        />
                        <button
                          type="button"
                          disabled={!molReference.trim() || recordMolReport.isPending}
                          onClick={() => { if (!recordMolReport.isPending) recordMolReport.mutate() }}
                          className="min-h-[40px] rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white disabled:opacity-50"
                        >
                          Record
                        </button>
                      </div>
                      {recordMolReport.isError && (
                        <p className="mt-1.5 text-xs text-danger-700">
                          {(recordMolReport.error as ApiError).message}
                        </p>
                      )}
                    </div>
                  )}
                  {d.kind === 'mol_48h' && d.satisfied_at && !d.evidence_note?.startsWith('Not required') && (
                    <p className="mt-1.5 text-xs text-success-700">
                      Reported to the Ministry {formatDateTime(d.satisfied_at)}
                    </p>
                  )}
                  {d.satisfied_at && d.evidence_note?.startsWith('Not required') && (
                    <p className="mt-1.5 text-xs text-neutral-600">
                      {d.evidence_note} · {formatDateTime(d.satisfied_at)}
                    </p>
                  )}
                  {!d.satisfied_at && can('ehs.statutory.manage') && (
                    <NotRequired deadlineId={d.id} onDone={touched} />
                  )}
                </li>
              ))}
            </ul>
          )}
          </>)}

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

          {/* Modified duties and the check-ins on them. Behind the medical
              key, and the panel hides itself rather than showing an empty
              state — "no plan" and "you cannot see the plan" must not look
              the same to a supervisor. */}
          <ReturnToWorkPanel incidentId={id} medical={inc.form_kind === 'medical'} />

          {/* Everything the system said about this incident, and to whom.
              Sits with the statutory panel rather than in the narrative: it
              answers "were they told", which is the same kind of question. */}
          <CommunicationLog docType="ehs_inc" docId={id} />

          {/* Every field ever changed on this incident. Beside the
              communications log because both answer the auditor's question
              rather than the investigator's. */}
          <ChangeHistory table="ehs_incidents" rowId={id} />
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
      <p className="font-mono text-[11px] uppercase tracking-wider text-neutral-400">
        {cause.cause_type === 'root' ? 'Root cause' : 'Immediate cause'}
      </p>
      <p className="mt-0.5 text-sm text-neutral-900">{cause.label}</p>
      {cause.note && <p className="mt-1 text-xs text-neutral-500">{cause.note}</p>}

      {/* ★ Openable, and carrying what it says to do. Round 4: "any root cause
          analysis, or capa completed as part of the drill or on any form shall
          display the capa description, and the information in the history of
          changes." It was a static row with a number and a title — a title is a
          label, the description is the instruction, and the change history is a
          click away only if the row is a link. It was not. */}
      {cause.actions.map((a) => (
        <Link key={a.id} to={`/actions/${a.id}`}
              className="mt-2 block rounded-md border border-dashed border-neutral-200 bg-neutral-50 px-2.5 py-2 text-xs hover:border-primary-300 hover:bg-primary-50/40">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-neutral-500">{a.action_no}</span>
            <span className="min-w-0 flex-1 font-medium text-neutral-800">{a.title}</span>
            <StatusBadge status={a.status} />
            {a.owner_name && <span className="text-neutral-500">{a.owner_name}</span>}
          </span>
          {a.description && (
            <span className="mt-1 block whitespace-pre-wrap text-neutral-600">
              {a.description}
            </span>
          )}
          <span className="mt-1 block text-[11px] text-neutral-400">
            Open it for who changed what, and when
          </span>
        </Link>
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

/**
 * Correcting the account of what happened.
 *
 * Round 6, box 4: "Supervisors shall have edit rights to all incidents, near
 * misses and hazard assessments to help manage them." The kind of incident and
 * how badly somebody was hurt are the classification, with its own step,
 * because they start statutory clocks. Saving a change withdraws HSE's review —
 * what was reviewed is no longer what the record says — and the change log
 * keeps every field under the editor's name.
 */
function EditReport({ incident, isHse, onDone, onCancel }: {
  incident: Incident; isHse: boolean; onDone: () => void; onCancel: () => void
}) {
  const local = incident.occurred_at ? new Date(incident.occurred_at) : null
  const pad = (n: number) => String(n).padStart(2, '0')
  const initial = {
    title: incident.title,
    date: local ? `${local.getFullYear()}-${pad(local.getMonth() + 1)}-${pad(local.getDate())}` : '',
    time: local ? `${pad(local.getHours())}:${pad(local.getMinutes())}` : '',
    location_id: incident.location_id ?? '',
    description: incident.description ?? '',
    equipment_involved: incident.equipment_involved ?? '',
    witnesses_note: incident.witnesses_note ?? '',
    immediate_action_taken: incident.immediate_action_taken ?? '',
  }
  const [form, setForm] = useState(initial)
  const set = (k: keyof typeof initial, v: string) => setForm((f) => ({ ...f, [k]: v }))

  // ★ Round-6 review: legal clocks counted from when it happened. Only HSE
  // moves the time then, and the clocks still owed are recounted from it — so
  // HSE says so before saving. Everybody else sees why the boxes are locked.
  // ★ Round 7, TC-12: by kind, as the server decides (counted_from_occurrence)
  // — not by matching a clock's start to the current time, which stopped
  // matching after the first correction and dropped the prompt and the lock.
  const fromOccurrence = (kind: string) =>
    kind === 'mol_48h' || (kind === 'wsib_form7' && !incident.employer_aware_at)
  const clocks = incident.deadlines.filter((d) => fromOccurrence(d.kind))
  const owed = clocks.filter((d) => !d.satisfied_at).length
  const timeLocked = clocks.length > 0 && !isHse
  const timeChanged = form.date !== initial.date || form.time !== initial.time
  const [recountOk, setRecountOk] = useState(false)
  const needsRecountOk = clocks.length > 0 && isHse && timeChanged

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {}
      for (const k of ['title', 'description', 'equipment_involved', 'witnesses_note',
                       'immediate_action_taken', 'location_id'] as const) {
        if (form[k] !== initial[k]) body[k] = form[k] === '' ? null : form[k]
      }
      if (form.date !== initial.date || form.time !== initial.time) {
        body.occurred_at = new Date(`${form.date}T${form.time || '00:00'}`).toISOString()
      }
      return api.patch(`/api/v1/incidents/${incident.id}`, body)
    },
    onSuccess: onDone,
  })

  const area = 'min-h-[80px] w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:border-primary-400 focus:outline-none'
  const box = 'min-h-[40px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:border-primary-400 focus:outline-none'
  return (
    <div className="mt-3 grid gap-3 rounded-xl border border-neutral-200 bg-neutral-50 p-3 sm:grid-cols-2">
      <label className="sm:col-span-2 text-xs font-medium text-neutral-700">Title
        <input className={box} value={form.title} onChange={(e) => set('title', e.target.value)} />
      </label>
      <label className="text-xs font-medium text-neutral-700">Date
        <input type="date" className={box} value={form.date} disabled={timeLocked}
               onChange={(e) => set('date', e.target.value)} />
      </label>
      <label className="text-xs font-medium text-neutral-700">Time
        <input type="time" className={box} value={form.time} disabled={timeLocked}
               onChange={(e) => set('time', e.target.value)} />
      </label>
      {timeLocked && (
        <p className="sm:col-span-2 text-xs text-neutral-600">
          Only HSE can change when this happened: statutory deadlines are counted from it.
        </p>
      )}
      {needsRecountOk && (
        <label className="sm:col-span-2 flex items-start gap-2 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning-800">
          <input type="checkbox" className="mt-0.5" checked={recountOk}
                 onChange={(e) => setRecountOk(e.target.checked)} />
          <span>
            Recount the statutory deadlines from the new time.{' '}
            {owed > 0 ? `${owed} still owed will move` : 'None is still owed, so none will move'}
            {clocks.length > owed ? `; ${clocks.length - owed} already met stay as recorded.` : '.'}
          </span>
        </label>
      )}
      <div className="sm:col-span-2 text-xs font-medium text-neutral-700">Where
        <LocationSelect value={form.location_id || null} onChange={(id) => set('location_id', id ?? '')} />
      </div>
      <label className="sm:col-span-2 text-xs font-medium text-neutral-700">What happened
        <textarea className={area} value={form.description} onChange={(e) => set('description', e.target.value)} />
      </label>
      <label className="text-xs font-medium text-neutral-700">Equipment involved
        <textarea className={area} value={form.equipment_involved} onChange={(e) => set('equipment_involved', e.target.value)} />
      </label>
      <label className="text-xs font-medium text-neutral-700">Witnesses
        <textarea className={area} value={form.witnesses_note} onChange={(e) => set('witnesses_note', e.target.value)} />
      </label>
      <label className="sm:col-span-2 text-xs font-medium text-neutral-700">What was done straight away
        <textarea className={area} value={form.immediate_action_taken} onChange={(e) => set('immediate_action_taken', e.target.value)} />
      </label>
      <div className="sm:col-span-2">
        <ErrorNote error={save.error} />
        <p className="mb-2 text-xs text-neutral-500">
          Saving withdraws HSE&rsquo;s review, which will need doing again. Every change is in the history.
        </p>
        <div className="flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={onCancel} disabled={save.isPending}>Cancel</button>
          <button type="button" className={primaryButton}
                  disabled={save.isPending || !form.title.trim() || !form.date
                    || (needsRecountOk && !recountOk)}
                  onClick={() => { if (!save.isPending) save.mutate() }}>
            {save.isPending ? 'Saving…' : 'Save the report'}
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * Stopping a legal clock that does not apply, with the reason.
 *
 * ★ Round 7: closing an incident and signing off its review wait for its
 * Ministry and WSIB clocks. A clock started on the first report can stop
 * applying — the injury reclassified as first aid — and without this the
 * incident could never close.
 */
function NotRequired({ deadlineId, onDone }: { deadlineId: string; onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const save = useMutation({
    mutationFn: () => api.post(`/api/v1/deadlines/${deadlineId}/not-required`, { reason: reason.trim() }),
    onSuccess: () => { setOpen(false); setReason(''); onDone() },
  })
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}
              className="mt-2 text-xs font-semibold text-neutral-600 hover:underline">
        Not required…
      </button>
    )
  }
  return (
    <div className="mt-2.5">
      <p className="text-xs text-neutral-500">
        Say why this obligation does not apply. It is kept on the record with the reason.
      </p>
      <div className="mt-2 flex gap-2">
        <input className="min-h-[40px] flex-1 rounded-lg border border-neutral-300 px-2.5 text-sm"
               placeholder="e.g. reclassified as first aid — no lost time"
               value={reason} onChange={(e) => setReason(e.target.value)} />
        <button type="button"
                disabled={reason.trim().length < 5 || save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}
                className="min-h-[40px] rounded-lg border border-neutral-300 px-3 text-sm font-semibold text-neutral-700 disabled:opacity-50">
          {save.isPending ? 'Saving…' : 'Mark not required'}
        </button>
      </div>
      {save.isError && (
        <p className="mt-1.5 text-xs text-danger-700">{(save.error as ApiError).message}</p>
      )}
    </div>
  )
}


interface KindOption {
  code: string; label: string; form_kind: string; asks_injury_class: boolean
}

/**
 * Changing what kind of incident it was, before it closes.
 *
 * Round 7 (answer box): "a medical incident may become a lost time injury after
 * the initial report is created. allow for the investigator to reclassify the
 * injury by selecting a different What kind of incident pick list before the
 * incident is closed." The kinds offered are those on the same form; the
 * server re-derives the injury class and the legal clocks, and says so.
 */
function ChangeKind({ incident, onDone }: { incident: Incident; onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [code, setCode] = useState(incident.incident_type_code ?? '')
  const kinds = useQuery({
    queryKey: ['incident-types', incident.form_kind],
    queryFn: () => api.get<KindOption[]>(`/api/v1/incidents/types?form_kind=${incident.form_kind}`),
    enabled: open,
  })
  const save = useMutation({
    mutationFn: () => api.post(`/api/v1/incidents/${incident.id}/reclassify`, { incident_type: code }),
    onSuccess: () => { setOpen(false); onDone() },
  })
  if (!open) {
    return (
      // ★ Round 8, TC-13: "the change the kind of incident is not obvious where
      // to do that, make it an orange button."
      <button type="button" onClick={() => { setCode(incident.incident_type_code ?? ''); setOpen(true) }}
              className="mb-2 min-h-[36px] rounded-lg bg-orange-500 px-3 text-xs font-semibold text-white hover:bg-orange-600">
        Change the kind of incident
      </button>
    )
  }
  return (
    <div className="mb-2 rounded-lg border border-neutral-200 bg-neutral-50 p-2.5">
      <select className="min-h-[40px] w-full rounded-lg border border-neutral-300 bg-white px-2 text-sm"
              aria-label="Kind of incident" value={code} onChange={(e) => setCode(e.target.value)}>
        {(kinds.data ?? []).map((k) => <option key={k.code} value={k.code}>{k.label}</option>)}
      </select>
      <p className="mt-1.5 text-xs text-neutral-600">
        The injury class and the legal clocks follow the new kind: a clock it owes is started, one
        it no longer owes is closed with the reason. HSE&rsquo;s review is withdrawn.
      </p>
      {kinds.isError && <p className="mt-1 text-xs text-danger-700">The kinds of incident could not be loaded.</p>}
      {save.isError && <p className="mt-1 text-xs text-danger-700">{(save.error as ApiError).message}</p>}
      <div className="mt-2 flex justify-end gap-2">
        <button type="button" className="min-h-[36px] rounded-lg border border-neutral-300 px-3 text-xs font-semibold"
                onClick={() => setOpen(false)} disabled={save.isPending}>Cancel</button>
        <button type="button"
                className="min-h-[36px] rounded-lg bg-primary-600 px-3 text-xs font-semibold text-white disabled:opacity-50"
                disabled={!code || code === incident.incident_type_code || save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          {save.isPending ? 'Saving…' : 'Change it'}
        </button>
      </div>
    </div>
  )
}


/** Why the server would not do it, said where the button was pressed. */
function Refusal({ title, error }: { title: string; error: unknown }) {
  return (
    <div role="alert" className="mt-2 flex items-start gap-2 rounded-lg border border-warning-300 bg-warning-50 px-3 py-2">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-warning-700" aria-hidden />
      <div className="text-xs text-warning-800">
        <p className="font-semibold">{title}</p>
        <p className="mt-0.5 whitespace-pre-line">
          {error instanceof ApiError ? error.message : 'Something went wrong. Try again.'}
        </p>
      </div>
    </div>
  )
}
