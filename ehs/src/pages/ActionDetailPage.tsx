/**
 * One corrective action: what it is for, what has been done, and whether the
 * control was ever confirmed to work.
 *
 * Reporting progress and verifying are separate acts by separate people, so
 * they are separate controls here rather than one "complete" button.
 */
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { daysOverdue, formatDate, formatDateTime } from '@/lib/formatDate'
import { StatusBadge } from '@/components/StatusBadge'
import { usePermissions } from '@/hooks/usePermissions'

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
  source_type: string; source_ref: string | null; status: string
  hierarchy_of_control: string | null; owner_name: string | null
  due_date: string; escalation_level: number; closed_at: string | null
  updates: Update[]; verifications: Verification[]
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
  const [evidence, setEvidence] = useState('')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['action', id],
    queryFn: () => api.get<ActionDetail>(`/api/v1/actions/${id}`),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['action', id] })
    queryClient.invalidateQueries({ queryKey: ['actions', 'mine'] })
  }

  const addUpdate = useMutation({
    mutationFn: () => api.post(`/api/v1/actions/${id}/updates`, {
      body: note,
      new_status: markDone ? 'pending_verification' : 'in_progress',
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

  return (
    <div className="p-4">
      <p className="font-mono text-xs text-neutral-500">{a.action_no}</p>
      <h1 className="mt-0.5 text-lg font-bold tracking-tight text-neutral-900">{a.title}</h1>
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
        {a.source_ref ? `Raised from ${a.source_ref} · ` : ''}
        Owner {a.owner_name ?? 'unassigned'} · due {formatDate(a.due_date)}
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
      <h2 className="mb-2 mt-5 text-xs font-bold uppercase tracking-wider text-neutral-400">
        Progress
      </h2>
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

      {/* ── Doing the work ──────────────────────────────────────────────── */}
      {a.status !== 'closed' && (
        <>
          <h2 className="mb-2 mt-5 text-xs font-bold uppercase tracking-wider text-neutral-400">
            Report progress
          </h2>
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
          <h2 className="mb-2 mt-5 text-xs font-bold uppercase tracking-wider text-neutral-400">
            Verify
          </h2>
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
    </div>
  )
}
