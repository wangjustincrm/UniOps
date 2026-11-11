/**
 * One corrective action: what it is for, what has been done, and whether the
 * control was ever confirmed to work.
 *
 * Reporting progress and verifying are separate acts by separate people, so
 * they are separate controls here rather than one "complete" button.
 */
import TabLink from '@/components/TabLink'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import { ShieldCheck } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import {
  PageHeader, SectionTitle,
} from '@/components/ui'
import { daysOverdue, formatDate, formatDateTime } from '@/lib/formatDate'
import { StatusBadge } from '@/components/StatusBadge'
import { sourceLink, SOURCE_NOUN } from '@/lib/sourceLink'
import { ChangeHistory } from '@/components/ChangeHistory'
import { usePermissions } from '@/hooks/usePermissions'
import { EMPTY_PERSON, PersonPicker, type PersonValue } from '@/components/PersonPicker'

interface Update {
  id: string; author_name: string | null; body: string
  new_status: string | null; created_at: string
}
interface Verification {
  id: string; verified_by_name: string | null; verified_at: string
  is_effective: boolean; evidence: string | null
}
interface ActionDetail {
  id: string; action_no: string; title: string; description: string | null
  source_type: string; source_id: string | null; source_ref: string | null; status: string
  hierarchy_of_control: string | null; owner_name: string | null
  due_date: string; escalation_level: number; closed_at: string | null
  work_request_no: string | null
  updates: Update[]; verifications: Verification[]
  /** Round 12, ACT-04: opened by whoever left the feedback it answers. */
  follow_only?: boolean
}

const HIERARCHY_LABEL: Record<string, string> = {
  elimination: 'Elimination', substitution: 'Substitution',
  engineering: 'Engineering control', administrative: 'Administrative control',
  ppe: 'Personal protective equipment',
}

export default function ActionDetailPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [note, setNote] = useState('')
  const [markDone, setMarkDone] = useState(false)
  const [workRequest, setWorkRequest] = useState('')
  const [evidence, setEvidence] = useState('')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['action', id],
    queryFn: () => api.get<ActionDetail>(`/api/v1/actions/${id}`),
  })

  // Same as the incident page: the route table can only name this tab from the
  // URL, so it reads `Action 4e8e5b7e` until the document arrives.
  useTabTitle(ehsRoutes, data?.action_no)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['action', id] })
    queryClient.invalidateQueries({ queryKey: ['actions', 'mine'] })
  }

  const addUpdate = useMutation({
    mutationFn: () => api.post(`/api/v1/actions/${id}/updates`, {
      body: note,
      new_status: markDone ? 'pending_verification' : 'in_progress',
      // Left blank it means "no change", never "there is no work request any
      // more" — the server treats it the same way.
      work_request_no: workRequest.trim() || null,
    }),
    onSuccess: () => { setNote(''); setMarkDone(false); invalidate() },
  })

  const verify = useMutation({
    mutationFn: (effective: boolean) =>
      api.post(`/api/v1/actions/${id}/verify`, { is_effective: effective, evidence: evidence || null }),
    onSuccess: () => { setEvidence(''); invalidate() },
  })

  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (isError) return <p className="p-4 text-sm text-danger-700">{(error as ApiError).message}</p>
  const a = data!
  const late = daysOverdue(a.due_date)
  const sourceHref = sourceLink(a.source_type, a.source_id, a.source_ref)

  return (
    <div className="p-4">
      <p className="font-mono text-xs text-neutral-500">{a.action_no}</p>
      <PageHeader title={a.title} />
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <StatusBadge status={a.status} />
        {late !== null && late > 0 && a.status !== 'closed' && (
          <span className="rounded-md bg-danger-600 px-2.5 py-1 text-xs font-medium text-white">
            {late}d late
          </span>
        )}
        {a.hierarchy_of_control && (
          <span className="rounded-md bg-neutral-100 px-2.5 py-1 text-xs text-neutral-600">
            {HIERARCHY_LABEL[a.hierarchy_of_control] ?? a.hierarchy_of_control}
          </span>
        )}
      </div>
      <p className="mt-2 text-xs text-neutral-500">
        {(a.source_ref || sourceHref) && (
          <>
            Raised from{' '}
            {/* A link where the record has a page, plain text where it does
                not. This was grey text at the weight of a caption, and it is
                the first thing anyone reading an action wants to open. */}
            {/* The number where the action stored one, and the kind of
                document where it did not — actions raised through the API had
                no reference at all until recently, and "Raised from" followed
                by nothing is not worth printing. */}
            {sourceHref ? (
              <TabLink to={sourceHref}
                    className="font-semibold text-neutral-900 underline underline-offset-2 hover:text-primary-700">
                {a.source_ref ?? SOURCE_NOUN[a.source_type] ?? 'the source record'}
              </TabLink>
            ) : (
              <span className="font-semibold text-neutral-900">{a.source_ref}</span>
            )}
            {' · '}
          </>
        )}
        Owner {a.owner_name ?? 'unassigned'} · due{' '}
        {/* The date is the thing people are chasing on this page, and it read
            at the same weight as the words around it. */}
        <span className={`font-bold ${(late ?? 0) > 0 ? "text-danger-700" : "text-neutral-900"}`}>
          {formatDate(a.due_date)}
        </span>
        {a.work_request_no && (
          <> · work request <span className="font-medium text-neutral-700">
            {a.work_request_no}</span></>
        )}
      </p>
      {a.description && <p className="mt-2 text-sm text-neutral-700">{a.description}</p>}

      {a.escalation_level >= 2 && a.status !== 'closed' && (
        <p className="mt-3 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning-700">
          {a.escalation_level >= 3
            ? 'Escalated to the HSE Manager.'
            : 'Escalated to the supervisor.'}
        </p>
      )}

      {/* ── History ─────────────────────────────────────────────────────── */}
      <SectionTitle className="mb-2 mt-5">Progress</SectionTitle>
      {a.updates.length === 0 && a.verifications.length === 0 && (
        <p className="text-sm text-neutral-500">Nothing recorded yet.</p>
      )}
      <ul className="flex flex-col gap-2">
        {a.updates.map((u) => (
          <li key={u.id} className="rounded-lg border border-neutral-200 bg-white p-3">
            <p className="text-sm text-neutral-800">{u.body}</p>
            <p className="mt-1 text-xs text-neutral-400">
              {u.author_name ?? 'Unknown'} · {formatDateTime(u.created_at)}
            </p>
          </li>
        ))}
        {a.verifications.map((v) => (
          <li key={v.id}
              className={`rounded-lg border p-3 ${
                v.is_effective ? 'border-success-500 bg-success-50' : 'border-danger-300 bg-danger-50'}`}>
            <p className="flex items-center gap-1.5 text-sm font-semibold text-neutral-900">
              <ShieldCheck className="h-4 w-4" aria-hidden />
              {v.is_effective ? 'Verified effective' : 'Verification failed'}
            </p>
            {v.evidence && <p className="mt-1 text-sm text-neutral-700">{v.evidence}</p>}
            <p className="mt-1 text-xs text-neutral-500">
              {v.verified_by_name ?? 'Unknown'} · {formatDateTime(v.verified_at)}
            </p>
          </li>
        ))}
      </ul>

      {a.follow_only && (
        <p className="mt-5 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
          You can follow this because it answers a comment you left on Feedback.
        </p>
      )}

      {/* ── Doing the work ──────────────────────────────────────────────── */}
      {a.status !== 'closed' && !a.follow_only && (
        <>
          <SectionTitle className="mb-2 mt-5">Report progress</SectionTitle>
          <textarea
            className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
            rows={3}
            placeholder="What has been done?"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <label className="mt-2 flex min-h-[44px] items-center gap-2.5 text-sm text-neutral-700">
            <input type="checkbox" className="h-4 w-4" checked={markDone}
                   onChange={(e) => setMarkDone(e.target.checked)} />
            The work is done — hand over for verification
          </label>

          {/* Most physical corrective actions are done by maintenance under
              their own work request. Without the number the two records cannot
              be tied together, and "has it been done" gets answered by asking
              somebody rather than by looking. */}
          <label className="mt-1 block text-xs font-medium text-neutral-700">
            Work request number
            <input
              className="mt-1 h-11 w-full rounded-lg border border-neutral-200 px-3 text-sm"
              value={workRequest}
              onChange={(e) => setWorkRequest(e.target.value)}
              placeholder={a.work_request_no ?? 'If maintenance raised one'}
            />
          </label>
          <button
            type="button"
            disabled={!note.trim() || addUpdate.isPending}
            onClick={() => { if (!addUpdate.isPending) addUpdate.mutate() }}
            className="mt-1 min-h-[44px] w-full rounded-lg bg-primary-600 px-4 text-sm font-semibold text-white disabled:opacity-50"
          >
            {addUpdate.isPending ? 'Saving…' : 'Save update'}
          </button>
          {addUpdate.isError && (
            <p className="mt-1.5 text-xs text-danger-700">{(addUpdate.error as ApiError).message}</p>
          )}
        </>
      )}

      {/* ── Verification — a different job, by a different person ───────── */}
      {a.status === 'pending_verification' && can('ehs.action.verify') && (
        <>
          <SectionTitle className="mb-2 mt-5">Verify</SectionTitle>
          <p className="mb-2 text-xs text-neutral-500">
            Confirm the control actually works — not that the work was done.
            Saying it is not effective reopens the action.
          </p>
          <textarea
            className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
            rows={2}
            placeholder="What did you check?"
            value={evidence}
            onChange={(e) => setEvidence(e.target.value)}
          />
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={verify.isPending}
              onClick={() => { if (!verify.isPending) verify.mutate(false) }}
              className="min-h-[44px] flex-1 rounded-lg border border-danger-300 bg-white px-4 text-sm font-semibold text-danger-700 disabled:opacity-50"
            >
              Not effective
            </button>
            <button
              type="button"
              disabled={verify.isPending}
              onClick={() => { if (!verify.isPending) verify.mutate(true) }}
              className="min-h-[44px] flex-[2] rounded-lg bg-success-700 px-4 text-sm font-semibold text-white disabled:opacity-50"
            >
              {verify.isPending ? 'Recording…' : 'Effective — close it'}
            </button>
          </div>
          {verify.isError && (
            <p className="mt-1.5 text-xs text-danger-700">{(verify.error as ApiError).message}</p>
          )}
        </>
      )}

      {/* Round 12, INC-02/03 and Q7: "only the supervisor or HSE role can
          reassign CAPA's." */}
      {can('ehs.action.write') && a.status !== 'closed' && a.status !== 'cancelled' && (
        <Reassign actionId={id} current={a.owner_name} onDone={invalidate} />
      )}

      <ChangeHistory table="ehs_actions" rowId={id} />
</div>
  )
}


function Reassign({ actionId, current, onDone }: {
  actionId: string; current: string | null; onDone: () => void
}) {
  const [open, setOpen] = useState(false)
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [reason, setReason] = useState('')
  const move = useMutation({
    mutationFn: () => api.post(`/api/v1/actions/${actionId}/reassign`,
                               { owner_id: person.userId, reason: reason.trim() }),
    onSuccess: () => { setOpen(false); setPerson(EMPTY_PERSON); setReason(''); onDone() },
  })
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}
              className="mt-5 min-h-[40px] text-sm font-medium text-primary-700 hover:underline">
        Reassign{current ? ` — now ${current}` : ''}
      </button>
    )
  }
  const ready = Boolean(person.userId) && reason.trim().length >= 3
  return (
    <section className="mt-5 rounded-lg border border-neutral-200 bg-white p-3">
      <SectionTitle className="mb-2">Reassign</SectionTitle>
      <p className="mb-2 text-xs text-neutral-500">
        The work and its task move to the person you choose. Who had it, who has it now and
        why go into its history, and onto the record it was raised from.
      </p>
      <div className="flex flex-col gap-2">
        <PersonPicker value={person} onChange={setPerson} allowExternal={false}
                      placeholder="Search for the person who will do it" />
        <input className="min-h-[44px] rounded-lg border border-neutral-300 px-3 text-sm"
               value={reason} onChange={(e) => setReason(e.target.value)}
               placeholder="Why it is moving" aria-label="Why it is moving" />
      </div>
      {move.isError && (
        <p className="mt-1.5 text-xs text-danger-700">{(move.error as ApiError).message}</p>
      )}
      <div className="mt-2 flex justify-end gap-2">
        <button type="button" onClick={() => setOpen(false)}
                className="min-h-[40px] rounded-lg border border-neutral-300 px-3 text-sm">Cancel</button>
        <button type="button" disabled={!ready || move.isPending}
                onClick={() => { if (ready && !move.isPending) move.mutate() }}
                className="min-h-[40px] rounded-lg bg-primary-600 px-3 text-sm font-semibold text-white disabled:opacity-50">
          {move.isPending ? 'Moving…' : 'Reassign'}
        </button>
      </div>
    </section>
  )
}
