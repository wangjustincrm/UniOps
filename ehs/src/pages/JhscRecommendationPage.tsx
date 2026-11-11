/**
 * One JHSC recommendation, and answering it.
 *
 * ★ Round 9 (open box): "when a worker or a supervisor opens a JHSC
 * recommendation assigned to them from the my actions register, open the
 * recommendation directly", and "when a worker, who has a jhsc recommendation
 * assigned to them, they can not open the recommendation and fill in what was
 * completed. give them a root cause corrective action form to complete and the
 * ability to add what will be done, the detail, the task owner, due date, and
 * priority, once it is submitted, remove it from the workers my actions
 * registry. allow the HSE role edit rights after this is submitted to make
 * corrections."
 *
 * The task in My Actions opens this page. Whoever the recommendation was sent
 * to may open it without the committee's own keys — being asked is the
 * permission; the server decides (`may_answer`, `may_manage`). The answer is
 * the corrective action form used on incidents, and submitting it raises the
 * action, records the reply (which stops the twenty-one days) and takes the
 * task out of everybody's My Actions.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import { api } from '@/lib/api'
import { formatDate, formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import {
  Card, ErrorNote, inputClass, LoadProblem, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'
import {
  ActionFields, actionIsComplete, emptyActionDraft, type ActionDraft,
} from '@/components/ActionFields'
import { CommunicationLog } from '@/components/CommunicationLog'
import { ActionHistory } from '@/components/ActionHistory'
import { VocabularySelect } from '@/components/VocabularyPicker'
import { ReplyBadge, type Recommendation } from '@/pages/JhscPage'

interface Detail extends Recommendation {
  meeting_no: string
  held_on: string | null
  may_answer: boolean
  may_manage: boolean
  may_set_status: boolean
  may_hse_verify?: boolean
  may_complete?: boolean
  hse_verified_at?: string | null
  hse_verified_by_name?: string | null
  action: {
    id: string; action_no: string; title: string; description: string | null
    owner_name: string | null; due_date: string; priority: string; status: string
  } | null
}

const OUTCOMES = [
  { key: 'accepted', label: 'Accepted' },
  { key: 'deferred', label: 'Deferred' },
  { key: 'declined', label: 'Declined' },
] as const

export default function JhscRecommendationPage() {
  const { id = '' } = useParams()
  const { can } = usePermissions()
  const rec = useQuery({
    queryKey: ['jhsc-recommendation', id],
    queryFn: () => api.get<Detail>(`/api/v1/jhsc/recommendations/${id}`),
    enabled: Boolean(id),
    retry: false,
  })
  useTabTitle(ehsRoutes, rec.data ? `${rec.data.meeting_no} #${rec.data.seq}` : undefined)

  if (rec.isPending) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (rec.isError || !rec.data) return <LoadProblem error={rec.error} noun="recommendation" />
  const r = rec.data

  return (
    <div className="mx-auto max-w-3xl p-4">
      <PageHeader title={`JHSC recommendation ${r.meeting_no} #${r.seq}`}
                  subtitle={r.held_on ? `Made at the meeting of ${formatDate(r.held_on)}` : undefined} />

      <Card>
        <div className="flex flex-wrap items-start gap-2">
          <p className="min-w-0 flex-1 whitespace-pre-wrap text-sm text-neutral-900">{r.recommendation}</p>
          <ReplyBadge rec={r} />
        </div>
        <dl className="mt-3 grid gap-x-4 gap-y-1.5 text-xs sm:grid-cols-2">
          {r.location_path && (
            <div><dt className="text-neutral-500">Where</dt>
              <dd className="text-neutral-800">{r.location_path.split('/').join(' › ')}</dd></div>
          )}
          {r.raised_by_name && (
            <div><dt className="text-neutral-500">Raised by</dt>
              <dd className="text-neutral-800">{r.raised_by_name}</dd></div>
          )}
          {r.sent_at && (
            <div><dt className="text-neutral-500">Sent to</dt>
              <dd className="text-neutral-800">
                {(r.sent_to ?? []).map((p) => p.name).filter(Boolean).join(', ')}
                {' · '}{formatDate(r.sent_at)}{r.sent_by_name ? ` by ${r.sent_by_name}` : ''}
              </dd></div>
          )}
          <div><dt className="text-neutral-500">Status</dt>
            <dd className="text-neutral-800">
              {r.may_set_status ? <StatusPicker rec={r} /> : (r.status_label ?? '—')}
            </dd></div>
          {r.reply_due_at && (
            <div><dt className="text-neutral-500">Written reply due</dt>
              <dd className="text-neutral-800">{formatDateTime(r.reply_due_at)} (OHSA s.9(20), 21 days)</dd></div>
          )}
        </dl>
      </Card>

      {r.may_answer && <AnswerForm recId={r.id} />}
      {r.responded_at && <Answered rec={r} />}
      {r.may_hse_verify && <HseVerify recId={r.id} />}
      {r.hse_verified_at && (
        <p className="mt-4 rounded-lg bg-success-50 px-3 py-2 text-sm text-success-800">
          Verified by HSE{r.hse_verified_by_name ? ` (${r.hse_verified_by_name})` : ''} on{' '}
          {formatDateTime(r.hse_verified_at)}.
        </p>
      )}
      <RecHistory recId={r.id} />

      {/* The correspondence about it — including who was told it was
          answered. Behind the committee's read key, like the meeting's. */}
      {(r.may_manage || can('ehs.jhsc.read')) && (
        <section className="mt-4">
          <SectionTitle className="mb-2">Notifications sent</SectionTitle>
          <CommunicationLog docType="ehs_jhsc" docId={r.id} />
        </section>
      )}
    </div>
  )
}

/** ★ Round 10, TC-11: "picking the current state of recommendations" from
 *  the list HSE keeps under Settings → Lists → JHSC recommendation status. */
function StatusPicker({ rec }: { rec: Detail }) {
  const queryClient = useQueryClient()
  const save = useMutation({
    mutationFn: (itemId: string) =>
      api.put(`/api/v1/jhsc/recommendations/${rec.id}/status`, { item_id: itemId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jhsc-recommendation', rec.id] })
      queryClient.invalidateQueries({ queryKey: ['jhsc-recommendations'] })
    },
  })
  return (
    <div className="max-w-xs">
      <VocabularySelect vocabulary="jhsc_rec_status" value={rec.status_item_id ?? null}
                        onChange={(item) => { if (item && !save.isPending) save.mutate(item.id) }}
                        placeholder="Not set" currentLabel={rec.status_label}
                        hideCodes={rec.may_complete ? undefined : ['completed']} />
      <ErrorNote error={save.error} />
    </div>
  )
}

function AnswerForm({ recId }: { recId: string }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<ActionDraft>(emptyActionDraft())
  const answer = useMutation({
    mutationFn: () => api.post(`/api/v1/jhsc/recommendations/${recId}/answer`, {
      title: draft.title.trim(),
      description: draft.description.trim() || null,
      owner_id: draft.ownerId,
      due_date: draft.dueDate,
      priority: draft.priority,
      hierarchy_of_control: draft.controlCode,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jhsc-recommendation', recId] })
      queryClient.invalidateQueries({ queryKey: ['jhsc-recommendations'] })
      // Out of My Actions, and the action raised into its owner's.
      queryClient.invalidateQueries({ queryKey: ['actions', 'mine'] })
    },
  })
  return (
    <Card className="mt-4">
      <SectionTitle className="mb-1">What will be done about it</SectionTitle>
      <p className="mb-3 text-xs text-neutral-500">
        The corrective action that answers this recommendation. Submitting it raises the
        action in its owner&rsquo;s My Actions, records the written reply, and takes this
        recommendation out of My Actions. HSE can correct it afterwards.
      </p>
      <ActionFields value={draft} onChange={setDraft} />
      <ErrorNote error={answer.error} />
      <div className="mt-3 flex justify-end">
        <button type="button" className={primaryButton}
                disabled={!actionIsComplete(draft) || answer.isPending}
                onClick={() => { if (!answer.isPending) answer.mutate() }}>
          {answer.isPending ? 'Submitting…' : 'Submit the corrective action'}
        </button>
      </div>
    </Card>
  )
}

function Answered({ rec }: { rec: Detail }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [response, setResponse] = useState(rec.response ?? '')
  const [outcome, setOutcome] = useState(rec.outcome ?? 'accepted')
  const save = useMutation({
    mutationFn: () => api.patch(`/api/v1/jhsc/recommendations/${rec.id}/response`,
                                { response: response.trim(), outcome }),
    onSuccess: () => {
      setEditing(false)
      queryClient.invalidateQueries({ queryKey: ['jhsc-recommendation', rec.id] })
      queryClient.invalidateQueries({ queryKey: ['jhsc-recommendations'] })
    },
  })
  return (
    <Card className="mt-4">
      <SectionTitle className="mb-2">The reply</SectionTitle>
      {editing ? (
        <>
          <textarea rows={4} className={`${inputClass} py-2`} value={response}
                    aria-label="Reply" onChange={(e) => setResponse(e.target.value)} />
          <select className={`${inputClass} mt-2`} value={outcome} aria-label="Outcome"
                  onChange={(e) => setOutcome(e.target.value)}>
            {OUTCOMES.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
          <ErrorNote error={save.error} />
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" className={secondaryButton} onClick={() => setEditing(false)}>Cancel</button>
            <button type="button" className={primaryButton}
                    disabled={!response.trim() || save.isPending}
                    onClick={() => { if (!save.isPending) save.mutate() }}>
              {save.isPending ? 'Saving…' : 'Save the correction'}
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="whitespace-pre-wrap text-sm text-neutral-800">{rec.response}</p>
          <p className="mt-1 text-xs text-neutral-500">
            {rec.responded_by_name} · {rec.responded_at && formatDateTime(rec.responded_at)}
          </p>
          {rec.may_manage && (
            <button type="button" className={`${secondaryButton} mt-2`} onClick={() => setEditing(true)}>
              Correct the reply
            </button>
          )}
        </>
      )}
      {rec.action && (
        <div className="mt-3 rounded-lg border border-neutral-200 px-3 py-2 text-sm">
          <Link to={`/actions/${rec.action.id}`} className="font-medium text-primary-700 hover:underline">
            {rec.action.action_no}
          </Link>{' '}
          {rec.action.title}
          {/* Round 12, TC-05: what the action says to do, not only its name. */}
          {rec.action.description && (
            <p className="mt-1 whitespace-pre-wrap text-xs text-neutral-700">{rec.action.description}</p>
          )}
          <span className="block text-xs text-neutral-500">
            {rec.action.owner_name ?? '—'} · due {formatDate(rec.action.due_date)} · {rec.action.priority} priority · {rec.action.status}
          </span>
        </div>
      )}
      {rec.action && <ActionHistory actionIds={[rec.action.id]} />}
    </Card>
  )
}


/** Round 12, step 11: "HSE role verifies the recommendations using the JHSC
 *  registry." Offered once it is answered and its corrective action is done. */
function HseVerify({ recId }: { recId: string }) {
  const queryClient = useQueryClient()
  const verify = useMutation({
    mutationFn: () => api.post(`/api/v1/jhsc/recommendations/${recId}/verify`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jhsc-recommendation', recId] })
      queryClient.invalidateQueries({ queryKey: ['jhsc-recommendations'] })
      queryClient.invalidateQueries({ queryKey: ['jhsc-rec-history', recId] })
    },
  })
  return (
    <Card className="mt-4">
      <SectionTitle className="mb-1">HSE verification</SectionTitle>
      <p className="mb-2 text-xs text-neutral-600">
        The reply is in and its corrective action is verified. Verifying here completes the
        recommendation.
      </p>
      <ErrorNote error={verify.error} />
      <button type="button" className={primaryButton} disabled={verify.isPending}
              onClick={() => { if (!verify.isPending) verify.mutate() }}>
        {verify.isPending ? 'Verifying…' : 'Verify and complete'}
      </button>
    </Card>
  )
}

/** Round 12: "when reviewing the recommendation, the history the
 *  recommendation has gone through should be listed for audit and tracking
 *  purposes." Its own steps here; the corrective action's under the reply. */
function RecHistory({ recId }: { recId: string }) {
  const history = useQuery({
    queryKey: ['jhsc-rec-history', recId],
    queryFn: () => api.get<{ at: string; what: string; by: string | null; text: string | null }[]>(
      `/api/v1/jhsc/recommendations/${recId}/history`),
  })
  if (!history.data?.length) return null
  return (
    <section className="mt-4">
      <SectionTitle className="mb-2">History</SectionTitle>
      <ol className="flex flex-col gap-1.5 border-l-2 border-neutral-200 pl-3">
        {history.data.map((h, i) => (
          <li key={i} className="text-xs">
            <span className="font-medium text-neutral-800">{h.what}</span>
            <span className="text-neutral-500">{' · '}{formatDateTime(h.at)}{h.by ? ` · ${h.by}` : ''}</span>
            {h.text && <p className="mt-0.5 whitespace-pre-wrap text-neutral-700">{h.text}</p>}
          </li>
        ))}
      </ol>
    </section>
  )
}
